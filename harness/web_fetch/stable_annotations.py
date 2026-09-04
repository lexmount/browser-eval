"""Reviewed acceptance annotations for ten dynamic demos and ten live lists.

Dynamic *loading* is not evidence of changing *content*. The fixed demo texts
below were transcribed from independent sources/<case>/reference.txt evidence,
not provider outputs. They are deliberate task requirements, not a claim that
all future source text will remain unchanged. No page header is inferred.

annotate_case is pure: it copies a case and preserves raw-reference fields and
old facts. Those remain captured_sample_diagnostic evidence, never live truth.
Changing this registry requires an explicit review; new capture values do not
automatically replace its fixed content anchors.
"""
from __future__ import annotations

from copy import deepcopy

from .stable_tasks import validate_acceptance_contract


REGISTRY_VERSION = 2
SNAPSHOT_METRIC_SCOPE = "captured_sample_diagnostic"


def _anchors(values, prefix):
    return [{"id": f"{prefix}-{i}", "alternatives": [value]} for i, value in enumerate(values, 1)]


def _tuples(text, fields, prefix):
    """One canonical owned tuple; the parser handles equivalent layouts."""
    anchors = []
    for i, block in enumerate(text.strip().split("\n\n"), 1):
        values = block.splitlines()
        if len(values) != len(fields):
            raise ValueError("Invalid reviewed annotation tuple")
        plain = "\n".join(values)
        anchors.append({"id": f"{prefix}-{i}", "alternatives": [plain]})
    return anchors


_QUOTES = _tuples('''“The world as we have created it is a process of our thinking. It cannot be changed without changing our thinking.”
by Albert Einstein
Tags: change deep-thoughts thinking world

“It is our choices, Harry, that show what we truly are, far more than our abilities.”
by J.K. Rowling
Tags: abilities choices

“There are only two ways to live your life. One is as though nothing is a miracle. The other is as though everything is a miracle.”
by Albert Einstein
Tags: inspirational life live miracle miracles

“The person, be it gentleman or lady, who has not pleasure in a good novel, must be intolerably stupid.”
by Jane Austen
Tags: aliteracy books classic humor

“Imperfection is beauty, madness is genius and it's better to be absolutely ridiculous than absolutely boring.”
by Marilyn Monroe
Tags: be-yourself inspirational

“Try not to become a man of success. Rather become a man of value.”
by Albert Einstein
Tags: adulthood success value

“It is better to be hated for what you are than to be loved for what you are not.”
by André Gide
Tags: life love

“I have not failed. I've just found 10,000 ways that won't work.”
by Thomas A. Edison
Tags: edison failure inspirational paraphrased

“A woman is like a tea bag; you never know how strong it is until it's in hot water.”
by Eleanor Roosevelt
Tags: misattributed-eleanor-roosevelt

“A day without sunshine is like, you know, night.”
by Steve Martin
Tags: humor obvious simile''', ("quote", "author", "tags"), "fixed-quote")

_PHONES = _tuples('''Motorola XOOM™ with Wi-Fi
The Next, Next Generation Experience the future with Motorola XOOM with Wi-Fi, the world's first tablet powered by Android 3.0 (Honeycomb).

MOTOROLA XOOM™
The Next, Next Generation Experience the future with MOTOROLA XOOM, the world's first tablet powered by Android 3.0 (Honeycomb).

MOTOROLA ATRIX™ 4G
MOTOROLA ATRIX 4G the world's most powerful smartphone.

Dell Streak 7
Introducing Dell™ Streak 7. Share photos, videos and movies together. It’s small enough to carry around, big enough to gather around.

Samsung Gem™
The Samsung Gem™ brings you everything that you would expect and more from a touch display smart phone – more apps, more features and a more affordable price.

Dell Venue
The Dell Venue; Your Personal Express Lane to Everything

Nexus S
Fast just got faster with Nexus S. A pure Google experience, Nexus S is the first phone to run Gingerbread (Android 2.3), the fastest version of Android yet.

LG Axis
Android Powered, Google Maps Navigation, 5 Customizable Home Screens

Samsung Galaxy Tab™
Feel Free to Tab™. The Samsung Galaxy Tab™ brings you an ultra-mobile entertainment experience through its 7” display, high-power processor and Adobe® Flash® Player compatibility.

Samsung Showcase™ a Galaxy S™ phone
The Samsung Showcase™ delivers a cinema quality experience like you’ve never seen before. Its innovative 4” touch display technology provides rich picture brilliance, even outdoors

DROID™ 2 Global by Motorola
The first smartphone with a 1.2 GHz processor and global capabilities.

DROID™ Pro by Motorola
The next generation of DOES.

MOTOROLA BRAVO™ with MOTOBLUR™
An experience to cheer about.

Motorola DEFY™ with MOTOBLUR™
Are you ready for everything life throws your way?

T-Mobile myTouch 4G
The T-Mobile myTouch 4G is a premium smartphone designed to deliver blazing fast 4G speeds so that you can video chat from practically anywhere, with or without Wi-Fi.

Samsung Mesmerize™ a Galaxy S™ phone
The Samsung Mesmerize™ delivers a cinema quality experience like you’ve never seen before. Its innovative 4” touch display technology provides rich picture brilliance,even outdoors

SANYO ZIO
The Sanyo Zio by Kyocera is an Android smartphone with a combination of ultra-sleek styling, strong performance and unprecedented value.

Samsung Transform™
The Samsung Transform™ brings you a fun way to customize your Android powered touch screen phone to just the way you like it through your favorite themed “Sprint ID Service Pack”.

T-Mobile G2
The T-Mobile G2 with Google is the first smartphone built for 4G speeds on T-Mobile's new network. Get the information you need, faster than you ever thought possible.

Motorola CHARM™ with MOTOBLUR™
Motorola CHARM fits easily in your pocket or palm. Includes MOTOBLUR service.''', ("name", "description"), "fixed-phone")

_OPERATIONS = _tuples('''POST
/pet/{petId}/uploadImage
uploads an image

POST
/pet
Add a new pet to the store

PUT
/pet
Update an existing pet

GET
/pet/findByStatus
Finds Pets by status

GET
/pet/findByTags
Finds Pets by tags

GET
/pet/{petId}
Find pet by ID

POST
/pet/{petId}
Updates a pet in the store with form data

DELETE
/pet/{petId}
Deletes a pet

GET
/store/inventory
Returns pet inventories by status

POST
/store/order
Place an order for a pet

GET
/store/order/{orderId}
Find purchase order by ID

DELETE
/store/order/{orderId}
Delete purchase order by ID

POST
/user/createWithList
Creates list of users with given input array

GET
/user/{username}
Get user by user name

PUT
/user/{username}
Updated user

DELETE
/user/{username}
Delete user

GET
/user/login
Logs user into the system

GET
/user/logout
Logs out current logged in user session

POST
/user/createWithArray
Creates list of users with given input array

POST
/user
Create user''', ("method", "path", "summary"), "fixed-operation")

_CATALOG_TEXT = _anchors([
    'Asus VivoBook X441NA-GA190 Chocolate Black, 14", Celeron N3450, 4GB, 128GB SSD, Endless OS, ENG kbd',
    'Prestigio SmartBook 133S Dark Grey, 13.3" FHD IPS, Celeron N3350 1.1GHz, 4GB, 32GB, Windows 10 Pro + Office 365 1 gadam',
    'Prestigio SmartBook 133S Gold, 13.3" FHD IPS, Celeron N3350 1.1GHz, 4GB, 32GB, Windows 10 Pro + Office 365 1 gadam',
    '15.6", Pentium N3520 2.16GHz, 4GB, 500GB, Linux',
    'Lenovo V110-15IAP, 15.6" HD, Celeron N3350 1.1GHz, 4GB, 128GB SSD, Windows 10 Home',
    'Asus VivoBook 15 X540NA-GQ008T Chocolate Black, 15.6" HD, Pentium N4200, 4GB, 500GB, Windows 10 Home, En kbd',
], "fixed-catalog-description")

_REVIEWS = _anchors([
    "Unique flavor and great energy boost. It's the perfect gamer's drink!",
    "Good flavor and keeps me energized. The bottle design is really fun.",
    "Excellent energy drink for gamers. The tropical flavor is refreshing.",
    "It’s fun, tastes good, and the energy boost is helpful during intense gaming sessions.",
    "The best sneakers I've bought in a long time. Stylish, comfortable and the leather quality is top-notch.",
    "The cherry cola flavor is a win. Keeps me energized and focused.",
    "These shoes are a hit with my twins. They love the lights and they seem to be quite durable.",
    "Excellent boots for outdoor adventures. They offer great support and are very comfortable.",
    "Nice design and good quality, but a bit too tight for me. Otherwise, it's a pretty cool beanie.",
    "Really enjoyed the citrus flavor and the energy boost it gives.",
    "Great concept, but the sizing is a bit off. Order a size larger.",
    "Delicious chocolates, and the box is pretty substantial. It'd make a nice gift.",
    "The boots are durable, but the laces could be better quality.",
    "This potion is a game changer. Love the energy boost and the flavor.",
    "Light, comfortable, and nice design. I'm buying another pair.",
    "Highly recommend! These shoes are great for evening walks. Kids love them!",
    "It's like a health potion for gamers! The energy boost is spot on.",
    "Really helps me focus during intense gaming marathons. The teal color is a nice touch.",
    "The shoes are nice but they didn't fit me well. I had to exchange for a larger size.",
    "Great taste, and the energy kick is awesome. Feels just like a strength potion.",
], "fixed-demo-review")

_PRODUCT_REVIEWS = _anchors([
    "Absolutely delicious! The orange flavor is my favorite.",
    "I bought these as a gift, and they were well received. Will definitely purchase again.",
    "Nice variety of flavors. The chocolate is rich and smooth.",
    "The cherry flavor is amazing. Will be buying more.",
    "A bit pricey, but the quality of the chocolate is worth it.",
], "fixed-product-review")

_FAMILIES = _anchors("Carettochelyidae Cheloniidae Chelydridae Dermatemydidae Dermochelyidae Emydidae Geoemydidae Kinosternidae Platysternidae Testudinidae Trionychidae Chelidae Pelomedusidae Podocnemididae".split(), "fixed-family")


def _demo(url, schema, count, anchors, stable_fields, structural_fields, note):
    return {"url": url, "track": "dynamic", "content_kind": "demonstration_content",
        "stable_fields": stable_fields, "structural_only_fields": structural_fields, "note": note,
        "contract": {"version": 1, "schema": schema, "minimum_records": count,
                     "stable_text_anchors": anchors}}


def _live(url, hosts, path, anchors=(), evidence=None):
    return {"url": url, "track": "challenge", "content_kind": "changing_public_list",
        "stable_fields": ["task_related_page_text"] if anchors else [],
        "anchor_evidence": evidence,
        "structural_only_fields": ["item_title", "detail_url", "rank", "price", "rating", "popularity"],
        "note": ("Only explicitly captured stable UI text is literal; item values and ranks are not fixed."
                 if anchors else "No stable non-item heading was retained in the declared source scope; no header is invented."),
        "contract": {"version": 1, "schema": "named_link", "minimum_records": 3,
                     "hosts": hosts, "entry_path": path, "stable_text_anchors": list(anchors)}}


# A closed, reviewed case registry, not inference from category/provider/fact IDs.
CASE_ANNOTATIONS = {
    "dynamic-quotes-api": _demo("https://quotes.toscrape.com/scroll", "quote", 10, _QUOTES,
        ["quote", "author", "tags"], [], "Asynchronously loaded fixed quote demonstration; preserve all ten quote/author/tag tuples."),
    "dynamic-products-api": _demo("https://www.scrapingcourse.com/javascript-rendering", "product", 12, [],
        [], ["name", "price"], "Demo catalog; no non-item header exists in the scoped reference. Names and prices are deliberately nonliteral, not asserted to be live-changing."),
    "dynamic-catalog": _demo("https://webscraper.io/test-sites/e-commerce/ajax/computers/laptops", "catalog_product", 6, _CATALOG_TEXT,
        ["description"], ["title", "price", "reviews"], "Keep six fixed demo specification texts; changing display title, price or review count does not change this contract."),
    "dynamic-delayed": _demo("https://quotes.toscrape.com/js-delayed/?delay=3000", "quote", 10, _QUOTES,
        ["quote", "author", "tags"], [], "Delayed rendering of the fixed quote demo, not a live quote ranking."),
    "dynamic-reviews-graphql": _demo("https://web-scraping.dev/reviews", "review", 20, _REVIEWS,
        ["text"], ["date"], "GraphQL loading is not content volatility: retain twenty seeded demo review texts; dates remain typed fields."),
    "dynamic-react-cart": _demo("https://react-shopping-cart-67954.firebaseapp.com/", "product", 16, [],
        [], ["name", "price", "financing", "shipping"], "Sixteen-product demo scope: at least fifteen financing fields and thirteen shipping fields on complete distinct records, independently captured in the reference. Values, titles and ordering remain nonliteral; not every product offers both."),
    "dynamic-angular-phones": _demo("https://angular.github.io/angular-phonecat/step-12/app/", "name_description", 20, _PHONES,
        ["name", "description"], [], "PhoneCat tutorial uses fixed historical sample devices; preserve all twenty name/description pairs."),
    "dynamic-swagger": _demo("https://petstore.swagger.io/", "api_operation", 20, _OPERATIONS,
        ["method", "path", "summary"], [], "Preserve the twenty captured Petstore demo operation tuples; no uncaptured Swagger header is required."),
    "dynamic-product-reviews": _demo("https://web-scraping.dev/product/1", "review", 5, _PRODUCT_REVIEWS,
        ["text"], ["date"], "Five seeded product-review texts are fixed demonstration content, regardless of embedded-state loading."),
    "dynamic-iframe": _demo("https://www.scrapethissite.com/pages/frames/", "taxonomy_family", 14, _FAMILIES,
        ["family"], [], "The iframe contains a fixed taxonomy demonstration, not a changing popularity list."),
    "challenge-01": _live("https://www.anthropologie.com/collection-decorative-candles",
        ["www.anthropologie.com", "www.anthropologie.com.cn"], r"/shop/[^/]+",
        _anchors(["Decorative Candles", "Price", "Color", "Brand"], "page-text"),
        "sources/challenge-01/stable-page.txt"),
    "challenge-02": _live("https://search.jd.com/Search?keyword=%E8%80%B3%E6%9C%BA&enc=utf-8",
        ["item.jd.com"], r"/\d+\.html", _anchors(["品牌", "综合", "销量"], "page-text"),
        "sources/challenge-02/stable-page.txt"),
    "challenge-03": _live("https://www.xiaohongshu.com/explore",
        ["www.xiaohongshu.com"], r"/explore/[a-fA-F0-9]+",
        _anchors(["穿搭", "美食", "彩妆", "影视"], "page-text"), "sources/challenge-03/stable-page.txt"),
    "challenge-04": _live("https://www.zhihu.com/hot", ["www.zhihu.com"], r"/question/\d+"),
    "challenge-05": _live("https://movie.douban.com/top250", ["movie.douban.com"], r"/subject/\d+",
        _anchors(["豆瓣电影 Top 250", "导演:", "主演:"], "page-text"), "sources/challenge-05/stable-page.txt"),
    "challenge-06": _live("https://www.dianping.com/shanghai/ch10", ["www.dianping.com"], r"/shop/[A-Za-z0-9]+",
        _anchors(["人均", "推荐菜", "条评价"], "record-label")),
    "challenge-07": _live("https://www.amazon.com/s?k=headphones", ["www.amazon.com"], r"(?:/[^/]+)?/dp/[A-Z0-9]{10}",
        _anchors(["headphones"], "search-context"), "sources/challenge-07/stable-page.txt"),
    "challenge-08": _live("https://www.reddit.com/r/programming/", ["www.reddit.com", "reddit.com"],
        r"/r/programming/comments/[a-z0-9]+(?:/[^/]+)?",
        _anchors(["r/programming", "Do you have a question? Check out /r/learnprogramming, /r/cscareerquestions, or Stack Overflow."], "community-text"),
        "sources/challenge-08/stable-page.txt"),
    "challenge-09": _live("https://www.imdb.com/chart/top/", ["www.imdb.com", "imdb.com"], r"/title/tt\d+",
        _anchors(["IMDb Top 250 movies", "As rated by regular IMDb voters."], "page-text"), "sources/challenge-09/stable-page.txt"),
    "challenge-10-justwatch": _live("https://www.justwatch.com/us/tv-shows", ["www.justwatch.com"], r"/us/tv-show/[^/]+",
        _anchors(["Discover TV shows and find where to watch online", "Where to watch TV series online?",
                  "You can also check out other streaming services to find out what’s available to buy, rent or watch for free."], "page-text"),
        "sources/challenge-10-justwatch/stable-page.txt"),
}

# Captured language variants are not guessed. The retained Amazon reference is
# Chinese while the independent current page is English; only shared query text
# is mandatory until equivalent fixed labels have independent bilingual evidence.
CASE_ANNOTATIONS['challenge-07']['note'] = ('Only the independently observed search context is a required text group. '
    'Lines containing extracted item labels cannot satisfy it. English filter labels are retained as evidence, not mandatory across locales. Product names and commercial values remain nonliteral.')
CASE_ANNOTATIONS['challenge-07']['contract']['stable_text_anchors'][0]['match_scope'] = 'non_record_lines'
CASE_ANNOTATIONS['challenge-06']['note'] = ('Required restaurant-card field labels come from the previously saved independent public-list reference. '
    'The labels, not restaurant names, dishes, per-capita prices or review counts, are matched.')


CASE_ANNOTATIONS['dynamic-react-cart']['contract']['field_minimum_records'] = {'financing': 15, 'shipping': 13}


def annotate_case(case: dict) -> dict:
    """Return an annotated deep copy; unknown cases stay unchanged.

    Adds acceptance_contract and source_benchmark.{stable_annotation,
    snapshot_metric_scope}. Does not change references, facts, capture hashes,
    task kind or any existing diagnostic evidence. Original task wording is
    retained separately when the displayed acceptance scope expands. No I/O.
    """
    result = deepcopy(case)
    rule = CASE_ANNOTATIONS.get(result.get("id"))
    if rule is None:
        return result
    if result.get("url") != rule["url"] or result.get("track") != rule["track"]:
        raise ValueError("Registered acceptance annotation has a different case URL or track")
    contract = deepcopy(rule["contract"])
    validate_acceptance_contract(contract)
    result["acceptance_contract"] = contract
    benchmark = result.setdefault("source_benchmark", {})
    if rule['track'] == 'challenge':
        benchmark.setdefault('captured_task_prompt', result.get('task_prompt', ''))
        labels = '；'.join(' / '.join(a['alternatives']) for a in contract['stable_text_anchors'])
        answer = '返回至少 3 条不同的具名本站详情入口；字段完整且链接符合登记的域名与路径规则。'
        answer += ('同时保留这些任务相关文字：' + labels + '。') if labels else '本例没有独立的稳定文字核对依据，文字覆盖不计算。'
        answer += '不固定条目名称、名次、价格、评分或热度；登录与错误页不能代替目标内容。'
        result['reference_answer'] = answer
        result['task_prompt'] = answer
    benchmark["snapshot_metric_scope"] = SNAPSHOT_METRIC_SCOPE
    benchmark["stable_annotation"] = {
        "registry": "stable_annotations", "version": REGISTRY_VERSION,
        "content_kind": rule["content_kind"], "stable_fields": deepcopy(rule["stable_fields"]),
        "structural_only_fields": deepcopy(rule["structural_only_fields"]),
        "anchor_evidence": (rule.get("anchor_evidence") or f"sources/{result['id']}/reference.txt"
                            if contract.get("stable_text_anchors") else None),
        "record_requirement": f"At least {contract['minimum_records']} complete unique records; additional legitimate records are allowed.",
        "task_decision_scope": "structure_and_explicit_stable_content",
        "word_overlap_scope": SNAPSHOT_METRIC_SCOPE, "old_fact_scope": SNAPSHOT_METRIC_SCOPE,
        "live_factual_correctness": None, "note": rule["note"],
    }
    return result


def bind_annotation_evidence(case, source):
    """Validate literal groups against their independent evidence before scoring."""
    import hashlib
    from .metrics import fact_in_text, normalize_for_match
    from .safety import contained_path
    annotation = case.get('source_benchmark', {}).get('stable_annotation')
    if not annotation or not annotation.get('anchor_evidence'):
        return
    raw = contained_path(source, annotation['anchor_evidence']).read_bytes()
    reference = normalize_for_match(raw.decode('utf-8'))
    for anchor in case['acceptance_contract'].get('stable_text_anchors', []):
        if not all(fact_in_text(reference, normalize_for_match(value)) for value in anchor['alternatives']):
            raise ValueError('Stable text lacks independent evidence: ' + case['id'] + '/' + anchor['id'])
    annotation['anchor_evidence_sha256'] = hashlib.sha256(raw).hexdigest()
