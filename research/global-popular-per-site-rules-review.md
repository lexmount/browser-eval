# 全球热门网站逐站规则审阅

> 本文件由 `research/global-popular-per-site-rules.json` 机械生成。规则证据只允许来自选定 URL 首次直接加载出的页面；不点击或递归访问详情页。

## 范围

- 规则复核前候选：58 个。
- 规则审阅后保留：57 个。
- 排除：1 个。
- 六类模型之外暂缓：0 个；不进入第一轮调用和分母。
- 最低字符数：不启用；兼容字段固定为 `min_content_chars: 1`、`length_gate_applicable: false`。
- 所有登记的稳定文字组均为必需项；不以价格、排名、数量或当前条目名称作为稳定真值。

标签分布：article 1、body_detail 3、content_list 24、landing_page 14、product_catalog 9、search_results 6。

## 规则审阅中发现的调整

- `global-popular-42-namu-wiki` 是 Wiki 门户首页，不是连续文章正文，因此从 `article` 改为 `landing_page`。
- `global-popular-27-spotify` 直接呈现播放列表条目，但没有逐条详情链接，因此使用 `named_item`，不得虚构链接。
- `global-popular-40-amazon-germany` 首次加载只呈现 Cookie/广告同意层；在不交互范围内没有商品目录，因此排除。
- 评分器暂不支持不等于页面不适配六类模型；只有真实页面无法被六类模型诚实表达时才列入特殊页面暂缓清单。

## 待审状态汇总

- `path_review_required_prior_evidence`：1 个
- `ready`：35 个
- `ready_from_prior_chromium_evidence`：7 个
- `ready_scorer_extension_required`：1 个
- `ready_with_declared_redirect_to_bbc_com`：1 个
- `ready_with_partial_page_error_exclusion`：1 个
- `record_title_ownership_review_required`：1 个
- `reference_capture_required`：1 个
- `relabel_ready_from_prior_chromium_evidence`：1 个
- `scorer_extension_and_captcha_risk_review_required`：1 个
- `scorer_extension_required`：5 个
- `scorer_extension_required_prior_evidence`：2 个

## 排除记录

- `global-popular-40-amazon-germany` — https://www.amazon.de/s?k=headphones — The selected URL's initial direct load presented only the cookie/advertising consent layer, not a product catalog; interaction is outside the accepted scope.

## 六类模型之外的暂缓案例

- 当前没有。

## article

| 案例 | 选定 URL | 规则模型 | 必需稳定文字组 | 结构或正文要求 | 明确不冻结 | 审阅状态 |
|---|---|---|---|---|---|---|
| `global-popular-03-wikipedia` | https://en.wikipedia.org/wiki/Web_scraping | `distributed_body` | Web scraping；History；Techniques；Legal issues | 最多 5 区；每区 ≥60%；除最多 1 区外均 ≥80% | donation_banner、last_modified_ui | `reference_capture_required` |

## body_detail

| 案例 | 选定 URL | 规则模型 | 必需稳定文字组 | 结构或正文要求 | 明确不冻结 | 审阅状态 |
|---|---|---|---|---|---|---|
| `global-popular-01-youtube` | https://www.youtube.com/watch?v=jNQXAC9IVRw | `detail_fields` | Me at the zoo；jawed | 字段：video_title、publisher_name | view_count、subscriber_count、recommendations、comments、relative_date | `ready` |
| `global-popular-11-netflix` | https://www.netflix.com/title/80057281 | `detail_fields` | Stranger Things / 怪奇物语；Episodes；More Details；Cast；Genres | 字段：title、synopsis、cast、creators、genres | plan_price、recommendations、season_marketing、availability_by_region | `ready` |
| `global-popular-17-weather` | https://weather.com/weather/hourbyhour/l/f892433d7660da170347398eb8e3d722d8d362fe7dd15af16ce88324e1b96e70 | `detail_fields` | New York City Hourly Forecast；Hourly；Temperature；Precipitation；Wind；Humidity | 字段：location、hourly_time、temperature_field、precipitation_field、wind_field | temperature_values、precipitation_values、wind_values、forecast_date | `ready` |

## content_list

| 案例 | 选定 URL | 规则模型 | 必需稳定文字组 | 结构或正文要求 | 明确不冻结 | 审阅状态 |
|---|---|---|---|---|---|---|
| `global-popular-02-reddit` | https://www.reddit.com/r/technology/ | `named_link` | r/technology / r/Technology；Community highlights / 社区置顶帖；Related communities / 相关社区 | 至少 3 条；主机：www.reddit.com、reddit.com；路径：`/r/technology/comments/[a-z0-9]+(?:/[^/?#]+)?` | post_titles、votes、comment_counts、authors、relative_dates | `ready` |
| `global-popular-04-yahoo-japan` | https://www.yahoo.co.jp/ | `named_link` | Yahoo! JAPAN；主なサービス；ニュース；社会的な取り組み | 至少 3 条；主机：news.yahoo.co.jp、sports.yahoo.co.jp、finance.yahoo.co.jp；路径：`/.+`；策略：`reviewed_yahoo_japan_content_hosts` | headlines、weather、market_values、rankings、advertisements | `ready_from_prior_chromium_evidence` |
| `global-popular-06-yahoo` | https://www.yahoo.com/ | `named_link` | Horoscope；Yahoo Scout；What to Watch；Today in History；What to play | 至少 3 条；主机：www.yahoo.com、news.yahoo.com、sports.yahoo.com、finance.yahoo.com；路径：`/.+`；策略：`reviewed_yahoo_content_hosts` | headlines、events、scores、market_values、advertisements | `ready_from_prior_chromium_evidence` |
| `global-popular-12-bilibili` | https://www.bilibili.com/v/popular/all/ | `named_link` | 综合热门；每周必看；入站必刷；全站音乐榜；各个领域中新奇好玩的优质内容都在这里 | 至少 3 条；主机：www.bilibili.com；路径：`/video/(?:BV[A-Za-z0-9]+\|av[0-9]+)` | video_titles、creators、play_counts、rank_order | `ready` |
| `global-popular-14-dzen` | https://dzen.ru/ | `named_link` | Дзен；Главное；Свежее；Все темы | 至少 3 条；主机：dzen.ru；路径：`/a/[A-Za-z0-9_-]+` | headlines、weather、exchange_rates、current_topics | `path_review_required_prior_evidence` |
| `global-popular-16-twitch` | https://www.twitch.tv/directory/category/just-chatting | `named_link` | Live Channels / 直播频道；Just Chatting / 谈天说地；All Channels / 所有频道 | 至少 3 条；主机：www.twitch.tv；路径：`/[A-Za-z0-9_]+` | channel_names、stream_titles、viewer_counts、live_status | `ready` |
| `global-popular-19-yahoo-news-japan` | https://news.yahoo.co.jp/ | `named_link` | Yahoo!ニュース；トピックス；全カテゴリのトピックス一覧；Yahoo!ニュース ライブ | 至少 3 条；主机：news.yahoo.co.jp；路径：`/articles/[A-Za-z0-9]+` | headlines、ranking_terms、viewer_counts、timestamps | `ready_from_prior_chromium_evidence` |
| `global-popular-20-fandom` | https://www.fandom.com/topics/games | `external_named_link` | Games / GAMES；Gaming News / GAMING NEWS | 至少 3 条；策略：`external_editorial_links_excluding_site_navigation` | headlines、source_names、timestamps、topic_tags | `scorer_extension_required_prior_evidence` |
| `global-popular-21-globo` | https://www.globo.com/ | `external_named_link` | Deu o que falar；Jornalismo；Economia；Esporte；Veja mais notícias | 至少 3 条；主机：g1.globo.com、oglobo.globo.com、ge.globo.com、globoplay.globo.com；策略：`reviewed_globo_editorial_hosts` | headlines、story_summaries、order | `ready_scorer_extension_required` |
| `global-popular-22-mail-ru` | https://news.mail.ru/ | `named_link` | Новости сегодня；Лента новостей；Политика；Экономика；Общество；Происшествия | 至少 3 条；主机：news.mail.ru；路径：`/[^/]+/[0-9]+` | headlines、timestamps、exchange_rates | `ready` |
| `global-popular-24-telegram-channel` | https://t.me/s/telegram | `named_link` | Telegram News；@telegram；The official Telegram on Telegram. Much recursion. Very Telegram. Wow. | 至少 3 条；主机：t.me；路径：`/telegram/[0-9]+` | subscriber_count、media_counts、message_text、message_dates | `ready` |
| `global-popular-25-github` | https://github.com/trending | `named_link` | Trending；See what the GitHub community is most excited about today.；Repositories；Developers；Spoken Language | 至少 3 条；主机：github.com；路径：`/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+` | repository_names、descriptions、languages、stars、rank_order | `ready_with_partial_page_error_exclusion` |
| `global-popular-27-spotify` | https://open.spotify.com/embed/playlist/37i9dQZF1DXcBWIGoYBM5M | `named_item` | Today’s Top Hits / Today's Top Hits；Spotify | 至少 3 条；不要求详情链接；字段：track_name、artist_name | track_names、artists、durations、track_order | `scorer_extension_required` |
| `global-popular-28-nytimes` | https://www.nytimes.com/international/section/world | `named_link` | World News；Africa；Americas；Asia；Europe；Middle East | 至少 3 条；主机：www.nytimes.com；路径：`/(?:[0-9]{4}/[0-9]{2}/[0-9]{2}/)?(?:world\|interactive)/.+` | headlines、summaries、authors、timestamps、order | `ready_from_prior_chromium_evidence` |
| `global-popular-30-bbc-uk` | https://www.bbc.co.uk/news | `named_link` | BBC News；News | 至少 3 条；主机：www.bbc.com；路径：`/news/articles/[a-z0-9]+` | headlines、summaries、timestamps、order | `ready_with_declared_redirect_to_bbc_com` |
| `global-popular-34-espn` | https://www.espn.com/nba/standings | `named_link` | NBA Standings；Eastern Conference；Western Conference；GLOSSARY / Glossary | 至少 3 条；主机：www.espn.com；路径：`/nba/team/_/name/[a-z0-9]+/[a-z0-9-]+` | season_label、team_order、wins、losses、percentages、streaks | `ready` |
| `global-popular-36-bbc-global` | https://www.bbc.com/news/world | `named_link` | World；FEATURES / Features；LATEST UPDATES / Latest updates | 至少 3 条；主机：www.bbc.com；路径：`/news/articles/[a-z0-9]+` | headlines、summaries、timestamps、order | `ready` |
| `global-popular-43-qq` | https://www.qq.com/ | `named_link` | 产品推荐；热点精选；今日热播；腾讯新闻出品；热门问答 | 至少 3 条；主机：news.qq.com；路径：`/rain/a/[A-Za-z0-9]+` | headlines、summaries、timestamps、order | `ready` |
| `global-popular-44-douyin` | https://www.douyin.com/jingxuan | `named_item` | 精选；公开课；游戏；二次元；音乐；影视 | 至少 3 条；不要求详情链接；字段：video_title、creator_name | video_titles、creators、play_counts、dates、order | `scorer_extension_required` |
| `global-popular-46-prime-video` | https://www.primevideo.com/-/en/collection/SVODTop10 | `named_link` | Top 10 in the US / 美国前十榜单；Popular movies / 热门电影；Popular TV / 热门电视剧；Top-rated movies / 评分最高的电影 | 至少 3 条；主机：www.primevideo.com；路径：`/-/[^/]+/detail/[A-Za-z0-9]+` | titles、rank_order、availability_by_region | `ready` |
| `global-popular-47-rutube` | https://rutube.ru/feeds/popular/ | `named_link` | Популярное；Каналы；Новинки блогеров | 至少 3 条；主机：rutube.ru；路径：`/video/[A-Za-z0-9]+` | video_titles、creators、views、relative_dates、order | `ready` |
| `global-popular-48-pinterest` | https://www.pinterest.com/ideas/ | `named_link` | Explore the best of Pinterest / 探索 Pinterest 上的最佳内容；Browse by category / 按类别浏览；Explore featured boards / 探索精选图板；What's new on Pinterest / Pinterest 的新增功能 | 至少 3 条；主机：www.pinterest.com；路径：`/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+` | featured_topics、board_titles、order | `ready` |
| `global-popular-59-bing-news` | https://www.bing.com/news/search | `external_named_link` | Top stories；World；Science；Technology；Entertainment；Business | 至少 3 条；策略：`external_news_result_links_excluding_bing_navigation` | headlines、publishers、timestamps、order | `scorer_extension_required` |
| `global-popular-60-roblox` | https://www.roblox.com/games/ | `named_link` | Charts；Computer；United States；Top Trending | 至少 3 条；主机：www.roblox.com；路径：`/games/[0-9]+(?:/[^/?#]+)?` | game_titles、ratings、player_counts、rank_order | `ready_from_prior_chromium_evidence` |

## landing_page

| 案例 | 选定 URL | 规则模型 | 必需稳定文字组 | 结构或正文要求 | 明确不冻结 | 审阅状态 |
|---|---|---|---|---|---|---|
| `global-popular-15-microsoft` | https://www.microsoft.com/en-us/ | `landing_sections` | Hi there, welcome to Microsoft；Your productivity, supercharged；Get to know AI and Copilot | 分布位置：hero、middle_product_section、later_ai_section | discounts、campaign_dates、featured_products | `ready` |
| `global-popular-31-apple` | https://www.apple.com/iphone/ | `landing_sections` | iPhone；Explore the lineup.；Why Apple is the best place to shop iPhone.；Get to know iPhone.；Cutting-Edge Cameras；Privacy | 分布位置：hero、lineup、shopping、feature_intro、camera、privacy | current_model_names、prices、preorder_dates、promotions | `ready` |
| `global-popular-32-docomo` | https://www.docomo.ne.jp/ | `landing_sections` | よく利用されるサービス・情報；スマートフォン・携帯電話；製品；料金・割引；金融・決済・ポイント | 分布位置：service_intro、mobile_section、product_section、pricing_section、finance_section | disaster_notices、campaigns、product_promotions | `ready` |
| `global-popular-39-disney-plus` | https://www.disneyplus.com/ | `landing_sections` | Disney+；Choose your plan；Watch the way you want；Frequently Asked Questions | 分布位置：hero_brand、plan_section、watching_features、faq | prices、discounts、bundle_terms、top_ten_titles、upcoming_titles | `ready` |
| `global-popular-42-namu-wiki` | https://namu.wiki/w/%EB%82%98%EB%AC%B4%EC%9C%84%ED%82%A4:%EB%8C%80%EB%AC%B8 | `landing_sections` | 나무위키에 오신 것을 환영합니다!；나무위키는 누구나 기여할 수 있는 위키입니다.；검증되지 않았거나 편향된 내용이 있을 수 있습니다.；나무위키 게시판；나무위키의 규정；나무위키의 도움말 | 分布位置：welcome、identity、caution、board、rules、help | last_modified_time、current_notices、projects、recent_changes | `relabel_ready_from_prior_chromium_evidence` |
| `global-popular-49-whatsapp` | https://www.whatsapp.com/ | `landing_sections` | Message privately；Never miss a moment with voice and video calls；Speak freely；Keep in touch with your groups；Transform your business | 分布位置：hero、calls、privacy、groups、business | download_promotions、announcements | `ready` |
| `global-popular-50-discord` | https://discord.com/ | `landing_sections` | GROUP CHAT THAT’S ALL FUN & GAMES / GROUP CHAT THAT'S ALL FUN & GAMES；MAKE YOUR GROUP CHATS MORE FUN；STREAM LIKE YOU’RE IN THE SAME ROOM / STREAM LIKE YOU'RE IN THE SAME ROOM；HOP IN WHEN YOU’RE FREE, NO NEED TO CALL / HOP IN WHEN YOU'RE FREE, NO NEED TO CALL | 分布位置：hero、customization、streaming、voice_text_chat | download_platform_label、campaigns | `ready_from_prior_chromium_evidence` |
| `global-popular-51-zoom` | https://www.zoom.com/ | `landing_sections` | Find out what's possible when work connects；One platform. Endless ways to work together.；Trusted by millions. Built for you.；Businesses achieve more with Zoom | 分布位置：hero、platform、trust、business_results | news_cards、customer_stories、promotions | `ready` |
| `global-popular-52-brave` | https://brave.com/ | `landing_sections` | The browser that puts you first；Private search；Powerful VPN；A Web with fewer ads；Winning on privacy, performance, and features | 分布位置：hero、search、vpn、web_principle、comparison | release_announcements、campaigns | `ready` |
| `global-popular-53-paypal` | https://www.paypal.com/us/home | `landing_sections` | Pay, send, and save smarter；Shop in stores and online；Pay now or pay over time.；Safety and privacy are our priority；Send money to just about anyone, anywhere | 分布位置：hero、shopping、installments、safety、send_money | cashback_rates、offer_brands、installment_terms、promotions | `ready` |
| `global-popular-54-deepseek` | https://www.deepseek.com/en/ | `landing_sections` | Into the Unknown；DeepSeek Web；API Platform；API Docs | 分布位置：brand_hero、web_entry、platform_entry、docs_entry | model_release_announcement、current_model_name | `ready` |
| `global-popular-55-telegram` | https://telegram.org/ | `landing_sections` | Why Telegram?；Simple；Private；Synced；Fast；Powerful；Open；Secure；Social | 分布位置：why_telegram、feature_grid | recent_news_titles、release_dates | `ready` |
| `global-popular-56-instructure` | https://www.instructure.com/ | `landing_sections` | Welcome to the most-visited education website in the world.；Solutions for every age, stage, and transitional moment；Explore the Instructure ecosystem；Ready for the next step? | 分布位置：identity、solutions、ecosystem、closing_action | article_cards、event_announcements、campaigns、numeric_claims | `ready` |
| `global-popular-57-duckduckgo` | https://duckduckgo.com/ | `landing_sections` | Switch to DuckDuckGo / 改用 DuckDuckGo；It’s private and free / 它是私密的且免费的；The browser that actively protects your personal information / 积极保护个人信息的浏览器；Take back control of your personal information / 重新掌控个人信息 | 分布位置：hero、privacy_claim、browser_section、control_section | latest_feature_promotions | `ready` |

## product_catalog

| 案例 | 选定 URL | 规则模型 | 必需稳定文字组 | 结构或正文要求 | 明确不冻结 | 审阅状态 |
|---|---|---|---|---|---|---|
| `global-popular-07-amazon-us` | https://www.amazon.com/s?k=headphones | `named_link` | headphones；Results；Filters | 至少 3 条；主机：www.amazon.com；路径：`(?:/[^/]+)?/dp/[A-Z0-9]{10}` | product_names、prices、ratings、review_counts、availability、result_count、order | `ready` |
| `global-popular-18-canva` | https://www.canva.com/templates/ | `named_link` | Free templates；Explore thousands of beautiful free templates.；Browse by category；New in Canva | 至少 3 条；主机：www.canva.com；路径：`/templates/[A-Za-z0-9_-]+` | template_names、featured_order、campaigns | `ready` |
| `global-popular-23-samsung` | https://www.samsung.com/us/smartphones/ | `named_link` | Smartphones；What's new；Switch to Galaxy；Compare | 至少 3 条；主机：www.samsung.com；路径：`/us/smartphones/[^/?#]+(?:/buy)?` | model_names、prices、promotions、availability、order | `record_title_ownership_review_required` |
| `global-popular-26-ebay` | https://www.ebay.com/deals | `named_link` | Deals；Featured Deals；Tech；Home & Garden | 至少 3 条；主机：www.ebay.com；路径：`/itm/[0-9]+` | product_names、prices、discounts、availability、order | `ready` |
| `global-popular-29-booking` | https://www.booking.com/city/us/new-york.html | `named_link` | Search hotels in New York / 搜索酒店：纽约，NY；Narrow your search / 缩小搜索范围；Enter your dates / 输入日期 | 至少 3 条；主机：www.booking.com；路径：`/hotel/us/[^/?#]+\.html` | hotel_names、prices、availability、property_count、dates、ratings、order | `ready` |
| `global-popular-33-amazon-india` | https://www.amazon.in/s?k=headphones | `named_link` | headphones；Results；Filters | 至少 3 条；主机：www.amazon.in；路径：`(?:/[^/]+)?/dp/[A-Z0-9]{10}` | product_names、prices、ratings、review_counts、availability、result_count、order | `ready` |
| `global-popular-35-amazon-japan` | https://www.amazon.co.jp/s?k=headphones | `named_link` | headphones；Results / 結果；Filters / 絞り込み / 筛选条件 | 至少 3 条；主机：www.amazon.co.jp；路径：`(?:/[^/]+)?/dp/[A-Z0-9]{10}` | product_names、prices、ratings、review_counts、availability、result_count、order | `ready` |
| `global-popular-41-rakuten` | https://www.rakuten.co.jp/ | `named_link` | ようこそ楽天市場へ；Rakuten AIで探す；楽天スーパーDEAL；人気商品ランキング | 至少 3 条；主机：item.rakuten.co.jp、product.rakuten.co.jp；路径：`/.+`；策略：`rakuten_product_hosts` | product_names、prices、discounts、points、rankings、order | `ready_from_prior_chromium_evidence` |
| `global-popular-45-usps` | https://store.usps.com/store/stamps | `named_link` | Stamps；Filter By；Product Type | 至少 3 条；主机：store.usps.com；路径：`/store/product/[^/?#]+` | product_names、prices、availability、order | `ready` |

## search_results

| 案例 | 选定 URL | 规则模型 | 必需稳定文字组 | 结构或正文要求 | 明确不冻结 | 审阅状态 |
|---|---|---|---|---|---|---|
| `global-popular-05-yandex` | https://yandex.ru/search/?text=OpenAI | `external_named_link` | OpenAI；Поиск | 至少 3 条；策略：`any_http_result_link_excluding_search_navigation` | result_titles、result_hosts、result_order、result_count、ads | `scorer_extension_required` |
| `global-popular-08-linkedin` | https://www.linkedin.com/jobs/search/?keywords=Software%20Engineer | `named_link` | Software Engineer；Date posted / 发布日期；Quick Apply / 快速申请；Get notified about new jobs / 新职位发布时接收通知 | 至少 3 条；主机：www.linkedin.com；路径：`/jobs/view/[^/?#]+` | job_titles、companies、locations、result_count、posting_dates、order | `ready` |
| `global-popular-09-baidu` | https://www.baidu.com/s?wd=OpenAI | `external_named_link` | OpenAI；百度一下；搜索工具；相关搜索 | 至少 3 条；策略：`baidu_result_redirect_or_external_http` | result_titles、result_order、result_count、hot_searches、ads | `scorer_extension_required` |
| `global-popular-10-naver` | https://search.naver.com/search.naver?query=OpenAI | `external_named_link` | OpenAI 검색 결과；관련문서；뉴스；블로그 | 至少 3 条；策略：`any_http_result_link_excluding_search_navigation` | result_titles、result_hosts、result_order、ads | `scorer_extension_required_prior_evidence` |
| `global-popular-37-ya-search` | https://ya.ru/search/?text=OpenAI | `external_named_link` | OpenAI；Поиск | 至少 3 条；策略：`any_http_result_link_excluding_search_navigation` | result_titles、result_hosts、result_order、result_count、ads | `scorer_extension_and_captcha_risk_review_required` |
| `global-popular-38-indeed` | https://www.indeed.com/jobs?q=software+engineer | `named_link` | software engineer jobs；Date posted；Keyword；Edit location input box label | 至少 3 条；主机：www.indeed.com；路径：`/(?:viewjob\|pagead/clk)` | job_titles、companies、locations、result_count、posting_dates、order | `ready` |

## 人工审阅结论栏

- 审阅人：待填写
- 审阅结论：待填写
- 未决案例：以各行 `review_status` 为准
- 允许进入供应商调用的条件：路径、稳定性、跨站搜索链接与 `named_item` 支持全部核对完成，机器规则验证通过。
