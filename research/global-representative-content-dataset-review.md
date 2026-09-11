# 全球热门网站代表性公开内容测试集

> 本文件由 `scripts/build_global_representative_dataset.py` 生成。它打包此前已经保留的逐站规则，不重新访问网页，也不使用供应商输出。该测试集不会在本轮运行。

## 范围结论

- 可执行案例：45 个。
- 暂不纳入：12 个；原因是既有审阅状态仍要求补证据、核路径或扩展评分器。
- 这些 URL 是人工选择的代表性公开内容页，不是 Similarweb 榜单指定的首页，因此与新首页主测试集分开。
- `named_link` 页面要求至少 3 个唯一名称＋详情链接；详情页和落地页使用 `page_identity`，要求命中全部登记的稳定文字组。
- 所有稳定文字仅来自选定 URL 的首次直接加载范围；不点击、不递归、不冻结当前列表成员或动态数值。

## 已打包案例

| 案例 | URL | 标签 | 验收 schema | 稳定文字组数 | 原审阅状态 |
|---|---|---|---|---:|---|
| `global-popular-01-youtube` | https://www.youtube.com/watch?v=jNQXAC9IVRw | `body_detail` | `page_identity` | 2 | `ready` |
| `global-popular-02-reddit` | https://www.reddit.com/r/technology/ | `content_list` | `named_link` | 3 | `ready` |
| `global-popular-04-yahoo-japan` | https://www.yahoo.co.jp/ | `content_list` | `named_link` | 4 | `ready_from_prior_chromium_evidence` |
| `global-popular-06-yahoo` | https://www.yahoo.com/ | `content_list` | `named_link` | 5 | `ready_from_prior_chromium_evidence` |
| `global-popular-07-amazon-us` | https://www.amazon.com/s?k=headphones | `product_catalog` | `named_link` | 3 | `ready` |
| `global-popular-08-linkedin` | https://www.linkedin.com/jobs/search/?keywords=Software%20Engineer | `search_results` | `named_link` | 4 | `ready` |
| `global-popular-11-netflix` | https://www.netflix.com/title/80057281 | `body_detail` | `page_identity` | 5 | `ready` |
| `global-popular-12-bilibili` | https://www.bilibili.com/v/popular/all/ | `content_list` | `named_link` | 5 | `ready` |
| `global-popular-15-microsoft` | https://www.microsoft.com/en-us/ | `landing_page` | `page_identity` | 3 | `ready` |
| `global-popular-16-twitch` | https://www.twitch.tv/directory/category/just-chatting | `content_list` | `named_link` | 3 | `ready` |
| `global-popular-17-weather` | https://weather.com/weather/hourbyhour/l/f892433d7660da170347398eb8e3d722d8d362fe7dd15af16ce88324e1b96e70 | `body_detail` | `page_identity` | 6 | `ready` |
| `global-popular-18-canva` | https://www.canva.com/templates/ | `product_catalog` | `named_link` | 4 | `ready` |
| `global-popular-19-yahoo-news-japan` | https://news.yahoo.co.jp/ | `content_list` | `named_link` | 4 | `ready_from_prior_chromium_evidence` |
| `global-popular-22-mail-ru` | https://news.mail.ru/ | `content_list` | `named_link` | 6 | `ready` |
| `global-popular-24-telegram-channel` | https://t.me/s/telegram | `content_list` | `named_link` | 3 | `ready` |
| `global-popular-25-github` | https://github.com/trending | `content_list` | `named_link` | 5 | `ready_with_partial_page_error_exclusion` |
| `global-popular-26-ebay` | https://www.ebay.com/deals | `product_catalog` | `named_link` | 4 | `ready` |
| `global-popular-28-nytimes` | https://www.nytimes.com/international/section/world | `content_list` | `named_link` | 6 | `ready_from_prior_chromium_evidence` |
| `global-popular-29-booking` | https://www.booking.com/city/us/new-york.html | `product_catalog` | `named_link` | 3 | `ready` |
| `global-popular-30-bbc-uk` | https://www.bbc.co.uk/news | `content_list` | `named_link` | 2 | `ready_with_declared_redirect_to_bbc_com` |
| `global-popular-31-apple` | https://www.apple.com/iphone/ | `landing_page` | `page_identity` | 6 | `ready` |
| `global-popular-32-docomo` | https://www.docomo.ne.jp/ | `landing_page` | `page_identity` | 5 | `ready` |
| `global-popular-33-amazon-india` | https://www.amazon.in/s?k=headphones | `product_catalog` | `named_link` | 3 | `ready` |
| `global-popular-34-espn` | https://www.espn.com/nba/standings | `content_list` | `named_link` | 4 | `ready` |
| `global-popular-35-amazon-japan` | https://www.amazon.co.jp/s?k=headphones | `product_catalog` | `named_link` | 3 | `ready` |
| `global-popular-36-bbc-global` | https://www.bbc.com/news/world | `content_list` | `named_link` | 3 | `ready` |
| `global-popular-38-indeed` | https://www.indeed.com/jobs?q=software+engineer | `search_results` | `named_link` | 4 | `ready` |
| `global-popular-39-disney-plus` | https://www.disneyplus.com/ | `landing_page` | `page_identity` | 4 | `ready` |
| `global-popular-41-rakuten` | https://www.rakuten.co.jp/ | `product_catalog` | `named_link` | 4 | `ready_from_prior_chromium_evidence` |
| `global-popular-42-namu-wiki` | https://namu.wiki/w/%EB%82%98%EB%AC%B4%EC%9C%84%ED%82%A4:%EB%8C%80%EB%AC%B8 | `landing_page` | `page_identity` | 6 | `relabel_ready_from_prior_chromium_evidence` |
| `global-popular-43-qq` | https://www.qq.com/ | `content_list` | `named_link` | 5 | `ready` |
| `global-popular-45-usps` | https://store.usps.com/store/stamps | `product_catalog` | `named_link` | 3 | `ready` |
| `global-popular-46-prime-video` | https://www.primevideo.com/-/en/collection/SVODTop10 | `content_list` | `named_link` | 4 | `ready` |
| `global-popular-47-rutube` | https://rutube.ru/feeds/popular/ | `content_list` | `named_link` | 3 | `ready` |
| `global-popular-48-pinterest` | https://www.pinterest.com/ideas/ | `content_list` | `named_link` | 4 | `ready` |
| `global-popular-49-whatsapp` | https://www.whatsapp.com/ | `landing_page` | `page_identity` | 5 | `ready` |
| `global-popular-50-discord` | https://discord.com/ | `landing_page` | `page_identity` | 4 | `ready_from_prior_chromium_evidence` |
| `global-popular-51-zoom` | https://www.zoom.com/ | `landing_page` | `page_identity` | 4 | `ready` |
| `global-popular-52-brave` | https://brave.com/ | `landing_page` | `page_identity` | 5 | `ready` |
| `global-popular-53-paypal` | https://www.paypal.com/us/home | `landing_page` | `page_identity` | 5 | `ready` |
| `global-popular-54-deepseek` | https://www.deepseek.com/en/ | `landing_page` | `page_identity` | 4 | `ready` |
| `global-popular-55-telegram` | https://telegram.org/ | `landing_page` | `page_identity` | 9 | `ready` |
| `global-popular-56-instructure` | https://www.instructure.com/ | `landing_page` | `page_identity` | 4 | `ready` |
| `global-popular-57-duckduckgo` | https://duckduckgo.com/ | `landing_page` | `page_identity` | 4 | `ready` |
| `global-popular-60-roblox` | https://www.roblox.com/games/ | `content_list` | `named_link` | 4 | `ready_from_prior_chromium_evidence` |

## 暂不纳入

- `global-popular-03-wikipedia` — `reference_capture_required` — https://en.wikipedia.org/wiki/Web_scraping
- `global-popular-05-yandex` — `scorer_extension_required` — https://yandex.ru/search/?text=OpenAI
- `global-popular-09-baidu` — `scorer_extension_required` — https://www.baidu.com/s?wd=OpenAI
- `global-popular-10-naver` — `scorer_extension_required_prior_evidence` — https://search.naver.com/search.naver?query=OpenAI
- `global-popular-14-dzen` — `path_review_required_prior_evidence` — https://dzen.ru/
- `global-popular-20-fandom` — `scorer_extension_required_prior_evidence` — https://www.fandom.com/topics/games
- `global-popular-21-globo` — `ready_scorer_extension_required` — https://www.globo.com/
- `global-popular-23-samsung` — `record_title_ownership_review_required` — https://www.samsung.com/us/smartphones/
- `global-popular-27-spotify` — `scorer_extension_required` — https://open.spotify.com/embed/playlist/37i9dQZF1DXcBWIGoYBM5M
- `global-popular-37-ya-search` — `scorer_extension_and_captcha_risk_review_required` — https://ya.ru/search/?text=OpenAI
- `global-popular-44-douyin` — `scorer_extension_required` — https://www.douyin.com/jingxuan
- `global-popular-59-bing-news` — `scorer_extension_required` — https://www.bing.com/news/search

## 人工核验栏

- 数据集：`evaluation/global-representative-content-dataset.json`
- 本轮供应商调用：未运行
- 后续若要运行：先重新核对页面证据新鲜度，并单独确认这套扩展组的运行范围。
