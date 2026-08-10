"""
Mutation Operator Taxonomy v2
================================
The single source of truth for operator names. `generator`, `ml.prioritizer`,
`explainability.test_gap` and `explainability.attribution` all import from here,
so a taxonomy edit can never again leave one of them silently out of sync.

Derived from mutmut 3.7.0's own dispatch table rather than from pattern-matching
observed diffs -- see docs/Research/experiments/mutmut_3.7_operator_catalogue.md
for the verbatim transcription and the wheel it came from. Every category below
maps to a specific operator function in mutmut/mutation/mutators.py; nothing is
declared that mutmut cannot produce.

Two levels, deliberately:

  OPERATORS (23)  fine-grained, one per distinct mutmut behaviour. Used for the
                  user-facing explanation, where precision is the whole point --
                  "the constant is never checked exactly" and "this argument is
                  never exercised as None" are different missing tests.

  FAMILIES (8)    coarse grouping, used for the ML one-hot vector. At a few
                  hundred training rows a 23-wide one-hot is mostly zeros and
                  trains badly; 8 families keep the vector learnable while every
                  slot stays populated.

Taxonomy v1 declared `statement_deletion` and `return_value_replacement`, which
mutmut cannot generate at all (they belong to mutpy/cosmic-ray), and its
`null_check_removal` rule searched for `''` while mutmut emits `""`. Those three
slots were structurally dead. They are gone.
"""

TAXONOMY_VERSION = "v2"

# --- fine-grained operators, grouped by the mutmut function that produces them ---

# operator_swap_op / _operator_mapping
COMPARISON_BOUNDARY = "comparison_boundary"            # <  <-> <=   >  <-> >=
EQUALITY_NEGATION = "equality_negation"                # == <-> !=
LOGICAL_CONNECTOR_SWAP = "logical_connector_swap"      # and <-> or
ARITHMETIC_REPLACEMENT = "arithmetic_replacement"      # + - * / // % ** and unary +/-
BITWISE_REPLACEMENT = "bitwise_replacement"            # & | ^ << >>
AUGMENTED_ASSIGN_SWAP = "augmented_assign_swap"        # += -> -=  etc.

# operator_augmented_assignment
AUGMENTED_ASSIGN_REMOVAL = "augmented_assign_removal"  # x += y -> x = y

# operator_number / operator_string / operator_name
NUMERIC_CONSTANT_CHANGE = "numeric_constant_change"    # n -> n+1
STRING_LITERAL_CHANGE = "string_literal_change"        # "s" -> "XXsXX" | case swap
BOOLEAN_CONSTANT_FLIP = "boolean_constant_flip"        # True <-> False
NAME_REPLACEMENT = "name_replacement"                  # deepcopy -> copy

# operator_assignment
ASSIGNMENT_TO_NONE = "assignment_to_none"              # a = b -> a = None ; a = None -> a = ""

# operator_remove_unary_ops
UNARY_OPERATOR_REMOVAL = "unary_operator_removal"      # not x -> x ; ~x -> x

# operator_keywords
IDENTITY_NEGATION = "identity_negation"                # is <-> is not
MEMBERSHIP_NEGATION = "membership_negation"            # in <-> not in
LOOP_CONTROL_SWAP = "loop_control_swap"                # break -> return ; continue -> break

# operator_arg_removal / operator_dict_arguments / string method swaps
ARGUMENT_REPLACEMENT = "argument_replacement"          # f(a, b) -> f(None, b)
ARGUMENT_REMOVAL = "argument_removal"                  # f(a, b) -> f(b)
STRING_METHOD_SWAP = "string_method_swap"              # .lower() -> .upper()
DICT_KEYWORD_CHANGE = "dict_keyword_change"            # dict(a=1) -> dict(aXX=1)

# operator_lambda / operator_match
LAMBDA_BODY_REPLACEMENT = "lambda_body_replacement"    # lambda: x -> lambda: None
MATCH_CASE_REMOVAL = "match_case_removal"              # drops one case arm

# residual -- a mutant the classifier could not attribute. Every occurrence is
# logged to reports/unclassified_mutations.jsonl rather than silently absorbed,
# because this rate is a headline quality number (v1: 73.5%, v2 target: <5%).
OTHER = "other"

OPERATORS = [
    COMPARISON_BOUNDARY,
    EQUALITY_NEGATION,
    LOGICAL_CONNECTOR_SWAP,
    ARITHMETIC_REPLACEMENT,
    BITWISE_REPLACEMENT,
    AUGMENTED_ASSIGN_SWAP,
    AUGMENTED_ASSIGN_REMOVAL,
    NUMERIC_CONSTANT_CHANGE,
    STRING_LITERAL_CHANGE,
    BOOLEAN_CONSTANT_FLIP,
    NAME_REPLACEMENT,
    ASSIGNMENT_TO_NONE,
    UNARY_OPERATOR_REMOVAL,
    IDENTITY_NEGATION,
    MEMBERSHIP_NEGATION,
    LOOP_CONTROL_SWAP,
    ARGUMENT_REPLACEMENT,
    ARGUMENT_REMOVAL,
    STRING_METHOD_SWAP,
    DICT_KEYWORD_CHANGE,
    LAMBDA_BODY_REPLACEMENT,
    MATCH_CASE_REMOVAL,
    OTHER,
]

# --- coarse families, used for the ML one-hot ---

FAMILY_COMPARISON = "comparison"
FAMILY_LOGICAL = "logical"
FAMILY_ARITHMETIC = "arithmetic"
FAMILY_CONSTANT = "constant"
FAMILY_CALL = "call"
FAMILY_ASSIGNMENT = "assignment"
FAMILY_CONTROL_FLOW = "control_flow"
FAMILY_OTHER = "other"

FAMILIES = [
    FAMILY_COMPARISON,
    FAMILY_LOGICAL,
    FAMILY_ARITHMETIC,
    FAMILY_CONSTANT,
    FAMILY_CALL,
    FAMILY_ASSIGNMENT,
    FAMILY_CONTROL_FLOW,
    FAMILY_OTHER,
]

FAMILY_BY_OPERATOR = {
    COMPARISON_BOUNDARY: FAMILY_COMPARISON,
    EQUALITY_NEGATION: FAMILY_COMPARISON,

    LOGICAL_CONNECTOR_SWAP: FAMILY_LOGICAL,
    UNARY_OPERATOR_REMOVAL: FAMILY_LOGICAL,
    IDENTITY_NEGATION: FAMILY_LOGICAL,
    MEMBERSHIP_NEGATION: FAMILY_LOGICAL,
    BOOLEAN_CONSTANT_FLIP: FAMILY_LOGICAL,

    ARITHMETIC_REPLACEMENT: FAMILY_ARITHMETIC,
    BITWISE_REPLACEMENT: FAMILY_ARITHMETIC,
    AUGMENTED_ASSIGN_SWAP: FAMILY_ARITHMETIC,
    AUGMENTED_ASSIGN_REMOVAL: FAMILY_ARITHMETIC,

    NUMERIC_CONSTANT_CHANGE: FAMILY_CONSTANT,
    STRING_LITERAL_CHANGE: FAMILY_CONSTANT,
    NAME_REPLACEMENT: FAMILY_CONSTANT,

    ARGUMENT_REPLACEMENT: FAMILY_CALL,
    ARGUMENT_REMOVAL: FAMILY_CALL,
    STRING_METHOD_SWAP: FAMILY_CALL,
    DICT_KEYWORD_CHANGE: FAMILY_CALL,

    ASSIGNMENT_TO_NONE: FAMILY_ASSIGNMENT,
    LAMBDA_BODY_REPLACEMENT: FAMILY_ASSIGNMENT,

    LOOP_CONTROL_SWAP: FAMILY_CONTROL_FLOW,
    MATCH_CASE_REMOVAL: FAMILY_CONTROL_FLOW,

    OTHER: FAMILY_OTHER,
}


def family_of(operator: str) -> str:
    """Coarse family for an operator. Unknown names fall back to `other` rather
    than raising -- a stale model artifact should degrade, not crash the run."""
    return FAMILY_BY_OPERATOR.get(operator, FAMILY_OTHER)


# Sanity: every declared operator must have a family. Cheap enough to assert at
# import; catches a half-finished taxonomy edit at the first import rather than
# three layers down inside feature vectorisation.
assert set(FAMILY_BY_OPERATOR) == set(OPERATORS), (
    "taxonomy drift: FAMILY_BY_OPERATOR and OPERATORS disagree on "
    f"{set(FAMILY_BY_OPERATOR) ^ set(OPERATORS)}"
)
assert set(FAMILY_BY_OPERATOR.values()) <= set(FAMILIES)
