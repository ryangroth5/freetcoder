"""Offline mode's recorded question, as the module strategy asks for it.

The JSON fixture beside this one is the same question for the monolithic path.
Both exist because offline mode has to answer whichever strategy is active:
`FakeLLM.complete_text` returning `str(dict)` to a path that lints and imports
its reply is the seam where "the tests pass" and "the product works" came
apart.

Kept deliberately boring. It is a demo and a Playwright fixture, not a
showcase, and e2e asserts on the title.
"""

TITLE = "Two Sum"

STATEMENT = """
Given an array of integers `nums` and an integer `target`, return the indices
of the two numbers that add up to `target`.

Exactly one valid answer exists, and you may not use the same element twice.
Return the indices in increasing order.

Example: `nums = [2, 7, 11, 15]`, `target = 9` returns `[0, 1]`, because
`nums[0] + nums[1] == 9`. For `nums = [3, 2, 4]`, `target = 6` the answer is
`[1, 2]`.
"""

CONSTRAINTS = """
- `2 <= len(nums) <= 1000`
- `-1000 <= nums[i] <= 1000`
- exactly one valid answer exists
"""

TOPICS = ["arrays", "hash maps"]

# Deliberately word-for-word with the JSON fixture's `hint_md`: the browser
# suite asserts on this copy, and a test that changes meaning when the fixture
# is swapped is testing the fixture rather than the UI.
HINT = "What have you already seen? A dictionary answers that in O(1)."

COMPLEXITY = ""

CLARIFICATIONS = [
    {
        "question": "May the same element be used twice?",
        "answer": "No. The two indices are always different.",
        "probe": {"nums": [3, 2, 4], "target": 6},
    },
    {
        "question": "In what order are the indices returned?",
        "answer": "Increasing, so the smaller index comes first.",
        "probe": {"nums": [2, 7, 11, 15], "target": 9},
    },
]


def two_sum(nums: list[int], target: int) -> list[int]:
    seen: dict[int, int] = {}
    for i, value in enumerate(nums):
        if target - value in seen:
            return [seen[target - value], i]
        seen[value] = i
    return []


#: The oracle. Named `two_sum` above because the candidate sees that name in
#: their scaffold: the probe takes the function name from `solution.__name__`,
#: so an alias is how a question gets a name that means something.
solution = two_sum


def brute_force(nums: list[int], target: int) -> list[int]:
    for i in range(len(nums)):
        for j in range(i + 1, len(nums)):
            if nums[i] + nums[j] == target:
                return [i, j]
    return []


def is_valid(nums: list[int], target: int) -> bool:
    if not 2 <= len(nums) <= 1000:
        return False
    if any(not -1000 <= n <= 1000 for n in nums):
        return False
    # Exactly one pair must work, or the question's own promise is broken.
    pairs = sum(
        1
        for i in range(len(nums))
        for j in range(i + 1, len(nums))
        if nums[i] + nums[j] == target
    )
    return pairs == 1


def generate_cases(rng):
    made = 0
    while made < 14:
        n = rng.randint(2, 12)
        nums = [rng.randint(-40, 40) for _ in range(n)]
        i, j = rng.sample(range(n), 2)
        target = nums[i] + nums[j]
        if is_valid(nums, target):
            made += 1
            yield {"nums": nums, "target": target}


EXAMPLES = [
    {"args": {"nums": [2, 7, 11, 15], "target": 9}, "why": "nums[0] + nums[1] == 9"},
    {"args": {"nums": [3, 2, 4], "target": 6}, "why": "the pair is not at the front"},
]
