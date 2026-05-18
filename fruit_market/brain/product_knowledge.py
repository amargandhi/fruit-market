"""Sales-facing product knowledge for the phone brain.

This is the local, deterministic copy of the fruit knowledge we also
load into Moss for semantic retrieval. The Gemini brain receives these
fields through tool outputs, so it can sound like a good market seller
without inventing varietals, flavor notes, or pairings.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProductKnowledge:
    variety: str
    short_description: str
    tasting_notes: str
    best_for: str
    ripeness_cues: str
    sales_tip: str
    pairings: str


_PRODUCTS: dict[str, ProductKnowledge] = {
    "apple": ProductKnowledge(
        variety="Cosmic Crisp-style red apple",
        short_description=(
            "A crisp, juicy red apple with a bright sweet-tart finish."
        ),
        tasting_notes=(
            "Crunchy first bite, honeyed sweetness, a clean tart edge, and "
            "enough juice to feel refreshing rather than heavy."
        ),
        best_for=(
            "Eating out of hand, slicing for lunch boxes, pairing with sharp "
            "cheddar, or serving with peanut butter."
        ),
        ripeness_cues=(
            "Look for firm skin, a deep red color, and no soft bruised spots. "
            "These hold their crunch well on the counter for a short demo day."
        ),
        sales_tip=(
            "If someone asks what tastes best, recommend the apples for a "
            "crisp snack: sweet enough for kids, tart enough for adults, and "
            "good with cheese or nut butter."
        ),
        pairings="Sharp cheddar, peanut butter, cinnamon, oats, pork, or arugula.",
    ),
    "banana": ProductKnowledge(
        variety="Cavendish banana",
        short_description=(
            "A mellow, creamy banana with gentle sweetness and a soft tropical aroma."
        ),
        tasting_notes=(
            "Light vanilla sweetness when yellow, richer honey notes as brown "
            "freckles appear, and a smooth texture that works well in smoothies."
        ),
        best_for=(
            "Breakfast, smoothies, lunch boxes, baking banana bread, or a quick "
            "pre-workout snack."
        ),
        ripeness_cues=(
            "Green tips mean firmer and less sweet; yellow is ready for snacking; "
            "brown freckles mean sweeter and better for baking."
        ),
        sales_tip=(
            "If someone wants something soft and sweet, recommend bananas. For "
            "same-day eating, point them to fully yellow fruit; for tomorrow, "
            "choose slightly green tips."
        ),
        pairings="Yogurt, oats, peanut butter, chocolate, berries, or coffee.",
    ),
    "orange": ProductKnowledge(
        variety="Navel orange",
        short_description="A bright, juicy citrus with sweet pulp and almost no seeds.",
        tasting_notes=(
            "Forward sweetness, tangy acid backbone, fragrant zest that "
            "perfumes the whole bag when you peel one."
        ),
        best_for="Fresh juice, snacking, fruit salads, marinades, or vinaigrettes.",
        ripeness_cues=(
            "Heavy for size means juicy. Firm but not rock-hard. Skin can be "
            "lightly mottled and still taste excellent."
        ),
        sales_tip=(
            "Recommend oranges to anyone fighting a cold or making fresh juice "
            "— two oranges yield about a cup of juice."
        ),
        pairings="Chocolate, almonds, fennel, basil, olive oil, or yogurt.",
    ),
    "lemon": ProductKnowledge(
        variety="Eureka lemon",
        short_description="A sharply acidic, fragrant yellow citrus with a fine-grained zest.",
        tasting_notes=(
            "Vivid sour bite, floral zest aroma, almost no sweetness — built "
            "to wake up other ingredients."
        ),
        best_for=(
            "Lemonade, dressings, finishing roast vegetables, baking, or "
            "cutting through rich fish dishes."
        ),
        ripeness_cues=(
            "Glossy skin, firm but with slight give when squeezed. Pale or "
            "greenish patches are fine if the lemon is heavy for its size."
        ),
        sales_tip=(
            "Pair lemons with anything heavy — fish, chicken, pasta. One "
            "lemon zested + juiced lifts a four-person meal."
        ),
        pairings="Olive oil, garlic, parsley, butter, fish, chicken, or honey.",
    ),
    "pear": ProductKnowledge(
        variety="Bartlett pear",
        short_description=(
            "A tender, buttery pear with floral sweetness and a soft, melting texture."
        ),
        tasting_notes=(
            "Honey-vanilla sweetness when ripe, almost grainy near the core, "
            "with a delicate perfume that intensifies as it ripens."
        ),
        best_for=(
            "Eating with a knife, slicing into salads, poaching in wine, or "
            "pairing with blue cheese on a board."
        ),
        ripeness_cues=(
            "Press gently near the stem — if it gives slightly, it's ready. "
            "Pears ripen from the inside out, so check daily once on the counter."
        ),
        sales_tip=(
            "Recommend pears for cheese boards or a fancy breakfast. They go "
            "from rock-hard to perfect in about two days at room temperature."
        ),
        pairings="Blue cheese, walnuts, honey, prosciutto, arugula, or red wine.",
    ),
    "grape": ProductKnowledge(
        variety="Red seedless table grape",
        short_description="A crisp, snappy red grape with balanced sweet-tart juice.",
        tasting_notes=(
            "Snap on the bite, a burst of sweet juice, gentle tart finish — "
            "satisfying without being cloying."
        ),
        best_for=(
            "Snacking, school lunches, cheese boards, freezing for a hot day, "
            "or roasting alongside chicken."
        ),
        ripeness_cues=(
            "Firmly attached to a green-pliable stem, no shriveled grapes, no "
            "white powdery mildew. A light bloom on the skin is good — natural wax."
        ),
        sales_tip=(
            "Push grapes for anyone with kids; they're the easiest fruit a child "
            "will eat without persuasion."
        ),
        pairings="Brie, manchego, walnuts, rosemary, chicken, or sparkling wine.",
    ),
    "strawberry": ProductKnowledge(
        variety="June-bearing strawberry",
        short_description=(
            "A jammy, perfumed berry with a deep red exterior and pale red interior."
        ),
        tasting_notes=(
            "Floral aroma you can smell from across the counter, sweet juicy "
            "flesh, mild tartness at the cap — pure summer."
        ),
        best_for=(
            "Eating out of the basket, slicing over yogurt or shortcake, jam, "
            "smoothies, or balsamic-macerated for dessert."
        ),
        ripeness_cues=(
            "Fully red shoulders (no white near the cap), fragrant, firm but "
            "not hard. Skip any with darkening soft spots."
        ),
        sales_tip=(
            "Strawberries don't ripen after picking — recommend they're eaten "
            "within 2 days for best flavor."
        ),
        pairings="Cream, balsamic, basil, mint, dark chocolate, or champagne.",
    ),
    "cherry": ProductKnowledge(
        variety="Bing cherry",
        short_description="A deep-red, plump stone fruit with intense sweet-tart juice.",
        tasting_notes=(
            "Bright cherry flavor with a wine-like depth, firm flesh that "
            "snaps clean off the pit, and almost no chalkiness."
        ),
        best_for=(
            "Snacking by the handful, pitting for clafoutis or pie, simmering "
            "with red wine for a sauce, or freezing for smoothies."
        ),
        ripeness_cues=(
            "Glossy nearly-black skin, firm flesh, fresh green stems. Soft or "
            "wrinkled cherries are past their best."
        ),
        sales_tip=(
            "Cherries are a treat-yourself fruit — recommend them for movie "
            "nights or as a high-end dessert topping."
        ),
        pairings="Dark chocolate, almonds, vanilla, goat cheese, port, or pork.",
    ),
    "peach": ProductKnowledge(
        variety="Yellow freestone peach",
        short_description=(
            "A fragrant, juicy stone fruit with a sweet honeyed flesh."
        ),
        tasting_notes=(
            "Heady summer-peach aroma, balanced sweetness with floral notes, "
            "tender flesh that runs with juice when fully ripe."
        ),
        best_for=(
            "Eating over the sink, grilling halves, baking into pie or "
            "cobbler, or slicing into a salad with burrata."
        ),
        ripeness_cues=(
            "Gentle give when pressed near the stem, a strong peach smell, "
            "background colour gone from green to gold."
        ),
        sales_tip=(
            "Suggest grilling them — 2 minutes per cut side caramelises the "
            "sugars and turns a snack into a dessert."
        ),
        pairings="Burrata, basil, prosciutto, vanilla ice cream, bourbon, or almonds.",
    ),
    "watermelon": ProductKnowledge(
        variety="Sugar Baby watermelon",
        short_description=(
            "A small, deep-green melon with crisp pink flesh and a refreshing sweetness."
        ),
        tasting_notes=(
            "Cold, juicy, mineral-clean sweetness with a faint cucumber edge. "
            "Best ice-cold from the fridge."
        ),
        best_for=(
            "Picnics, hot-day snacking, melon salads with feta and mint, or "
            "blending into agua fresca."
        ),
        ripeness_cues=(
            "Heavy for size, hollow thump when tapped, a creamy-yellow field "
            "spot where it rested on the ground."
        ),
        sales_tip=(
            "Watermelons are the easiest crowd-pleaser at any gathering — "
            "one feeds 6-8 people."
        ),
        pairings="Feta, mint, lime, basil, chili-lime salt, or tequila.",
    ),
    "pineapple": ProductKnowledge(
        variety="MD-2 golden pineapple",
        short_description=(
            "A tropical, fragrant fruit with bright golden flesh and a fibrous core."
        ),
        tasting_notes=(
            "Sweet upfront, tangy mid-palate, slight enzyme tingle at the "
            "finish that means it's freshly cut."
        ),
        best_for=(
            "Fruit platters, grilling, blending into smoothies or piña "
            "coladas, salsas, or as a pizza topping."
        ),
        ripeness_cues=(
            "Sweet aroma at the base, slight give when pressed, an interior "
            "leaf that pulls free with a gentle tug."
        ),
        sales_tip=(
            "If asked how to cut one, suggest slicing crown + base off, "
            "standing it up, and slicing the skin down the sides."
        ),
        pairings="Lime, mint, rum, chili, coconut, ham, or cottage cheese.",
    ),
    "mango": ProductKnowledge(
        variety="Ataulfo (honey) mango",
        short_description=(
            "A small, golden-fleshed mango with buttery texture and intense sweetness."
        ),
        tasting_notes=(
            "Mango-candy sweetness, hints of citrus and peach, almost no "
            "fibrous strings, soft enough to scoop with a spoon."
        ),
        best_for=(
            "Eating with a spoon out of the skin, lassi, salsa with red onion "
            "and lime, or slicing over sticky rice with coconut milk."
        ),
        ripeness_cues=(
            "Skin turns from yellow to deep gold with wrinkles, gentle give "
            "when squeezed, fragrant near the stem."
        ),
        sales_tip=(
            "Recommend these over red mangoes for first-time eaters — sweeter, "
            "less fibrous, easier to peel."
        ),
        pairings="Lime, chili, sticky rice, coconut, basil, mint, or yogurt.",
    ),
    "avocado": ProductKnowledge(
        variety="Hass avocado",
        short_description=(
            "A creamy, mild fruit with a buttery texture and nutty flavour."
        ),
        tasting_notes=(
            "Rich, almost custard-like flesh, mild grassy notes, no real "
            "sweetness — built to carry salt, lime, and chili."
        ),
        best_for=(
            "Toast, guacamole, salads, sushi, smoothies for richness, or "
            "halved with lime + flaky salt."
        ),
        ripeness_cues=(
            "Slight give under gentle pressure (don't press with one finger — "
            "use the whole palm). Skin should be near-black, not green."
        ),
        sales_tip=(
            "Suggest buying two: one ripe for today, one firm for 2 days out. "
            "Speeds ripening by storing with a banana."
        ),
        pairings="Lime, cilantro, tomato, red onion, eggs, sourdough, or chili crisp.",
    ),
}


# Aliases the kiosk / Gemini might encounter — map to canonical
# keys above. The lookup also strips a trailing 's', so we only
# need entries for words the plural rule doesn't reach.
_ALIASES: dict[str, str] = {
    "apples":      "apple",
    "bananas":     "banana",
    "naner":       "banana",
    "nanas":       "banana",
    "oranges":     "orange",
    "lemons":      "lemon",
    "pears":       "pear",
    "grapes":      "grape",
    "strawberries":"strawberry",
    "berries":     "strawberry",
    "cherries":    "cherry",
    "peaches":     "peach",
    "watermelons": "watermelon",
    "melon":       "watermelon",
    "pineapples":  "pineapple",
    "mangoes":     "mango",
    "mangos":      "mango",
    "avocados":    "avocado",
    "avo":         "avocado",
}


def knowledge_for(name: str) -> ProductKnowledge | None:
    """Resolve a fruit name (or alias) to its sales knowledge."""

    if not name:
        return None
    raw = name.strip().lower()
    if raw in _PRODUCTS:
        return _PRODUCTS[raw]
    if raw in _ALIASES:
        return _PRODUCTS.get(_ALIASES[raw])
    # Naive de-pluralisation last so explicit aliases win.
    return _PRODUCTS.get(raw.removesuffix("s"))


def known_fruits() -> list[str]:
    """Sorted list of canonical fruit names — useful for the
    teach UI's autocomplete or for a sanity check that the
    catalog only seeds known-good defaults."""

    return sorted(_PRODUCTS.keys())
