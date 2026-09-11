# 全球热门域名首页规则审阅

> 本文件由 `scripts/build_global_homepage_suite.py` 从最终独立 Chromium 证据机械生成。默认请求榜单域名根首页；只有独立观察证明根首页语言会变化时，才使用同站固定语言首页 URL。禁止人工替换成文章、视频或商品详情页。

## 范围

- 最终纳入：52 个；非成人候选排除：38 个。目标数量不是配额。
- 成人域名已在候选生成阶段按用户政策省略，未访问，也未写入本文件。
- 登录页、空白页、错误页、验证码/安全验证页和当前地区不可用页均排除。
- `named_link` 要求至少 3 个唯一具名详情链接；`page_identity` 要求命中全部登记的稳定身份/功能文字。
- 不设置实质最低字符数；兼容字段为 `min_content_chars: 1`。不冻结当前条目、排名、价格、日期或数量。

## 冻结前可视抽查

- 抽查并成功观察：10 个；未通过当前可视浏览器复查：1 个。
- 抽查记录：`research/global-homepage-visual-spot-check-2026-09-11.json`。它补充而不替代完整无头 Chromium 证据。
- YouTube 在可视匿名首页呈现公开推荐视频流，因此标签修正为 `content_list`；视频标题等动态成员仍不作为固定得分点。
- 对独立观察证明会出现地区/语言变体的首页，改用已验证的同站固定语言 URL；没有可靠固定方式的 Roblox 明确暂缓，不混合多语言规则。
- 每项契约只登记所选首页独立观察到的一种语言；不允许用多语种候选词拼接成一次通过。

## 逐站规则

| 案例 | 请求 URL → 观察到的最终 URL | 标签 | 模型 | 稳定文字组 | 证据中匹配的具名链接 |
|---|---|---|---|---|---:|
| `global-homepage-001-google-com` | https://www.google.com/?hl=en → https://www.google.com.hk/?hl=en | `entry_page` | `page_identity` | Google；About；Advertising；Business；Privacy；Terms | 0 |
| `global-homepage-002-youtube-com` | https://www.youtube.com/?hl=en&gl=US&persist_hl=1 → https://www.youtube.com/ | `content_list` | `page_identity` | YouTube Home；Guide；Search；Settings；Sign in | 0 |
| `global-homepage-005-chatgpt-com` | https://chatgpt.com/ → https://chatgpt.com/ | `entry_page` | `page_identity` | ChatGPT；Where should we begin?；Chat with ChatGPT；New chat | 0 |
| `global-homepage-006-reddit-com` | https://reddit.com/ → https://www.reddit.com/?solution=b29b419a63795e65b29b419a63795e65&js_challenge=1&jsc_token=7afd7253fec22262ff1c52b1703fe9ec353b328cf9977367be9d31a482035156&jsc_orig_r= | `content_list` | `named_link` | POPULAR COMMUNITIES；Best；Hot | 28 |
| `global-homepage-008-whatsapp-com` | https://whatsapp.com/ → https://www.whatsapp.com/ | `landing_page` | `page_identity` | Message privately；Never miss a moment with voice and video calls；Keep in touch with your groups；Transform your business | 0 |
| `global-homepage-010-bing-com` | https://bing.com/ → https://www.bing.com/?toWww=1&redig=664889C106914BE786F166BF5F44DAFE | `entry_page` | `page_identity` | Microsoft Bing；Images；Maps；Search using an image；Sign in | 0 |
| `global-homepage-011-wikipedia-org` | https://wikipedia.org/ → https://www.wikipedia.org/ | `entry_page` | `page_identity` | Wikipedia；The Free Encyclopedia；English；日本語 | 0 |
| `global-homepage-012-gemini-google-com` | https://gemini.google.com/ → https://gemini.google.com/app | `entry_page` | `page_identity` | Gemini；Conversation with Gemini；Meet Gemini, your personal AI assistant | 0 |
| `global-homepage-013-yahoo-co-jp` | https://yahoo.co.jp/ → https://www.yahoo.co.jp/ | `content_list` | `named_link` | Yahoo! JAPAN；主なサービス；お知らせ；ニュース | 8 |
| `global-homepage-015-yahoo-com` | https://www.yahoo.com/?p=us → https://www.yahoo.com/?p=us | `content_list` | `named_link` | Search the web；News；Finance；Sports；Trending | 3 |
| `global-homepage-016-amazon-com` | https://amazon.com/ → https://www.amazon.com/ | `product_catalog` | `named_link` | Amazon；Today's Deals；Customer Service | 11 |
| `global-homepage-018-baidu-com` | https://baidu.com/ → https://www.baidu.com/ | `entry_page` | `page_identity` | 百度一下；新闻；地图；更多 | 0 |
| `global-homepage-020-naver-com` | https://naver.com/ → https://www.naver.com/ | `content_list` | `named_link` | NAVER；공지사항；Partners；Developers | 10 |
| `global-homepage-021-netflix-com` | https://netflix.com/ → https://www.netflix.com/ | `landing_page` | `page_identity` | Unlimited movies, TV shows, and more；More Reasons to Join；Frequently Asked Questions | 0 |
| `global-homepage-023-cloud-microsoft` | https://cloud.microsoft/ → https://m365.cloud.microsoft/ | `landing_page` | `page_identity` | Work smarter across Microsoft 365 with Copilot；Popular prompts to try；Jump into your favorite app | 0 |
| `global-homepage-025-bilibili-com` | https://bilibili.com/ → https://www.bilibili.com/ | `content_list` | `named_link` | 首页；番剧；直播；热门 | 14 |
| `global-homepage-027-dzen-ru` | https://dzen.ru/ → https://dzen.ru/ | `content_list` | `named_link` | Поиск Яндекса；Новости；Статьи；Видео | 25 |
| `global-homepage-028-microsoft-com` | https://microsoft.com/ → https://www.microsoft.com/en-us | `landing_page` | `page_identity` | Microsoft；Microsoft 365；Windows；Surface；XBOX | 0 |
| `global-homepage-029-twitch-tv` | https://www.twitch.tv/?lang=en → https://www.twitch.tv/?lang=en | `content_list` | `named_link` | Live Channels；Live on Twitch；Categories we think you’ll like | 3 |
| `global-homepage-030-weather-com` | https://weather.com/ → https://weather.com/ | `entry_page` | `page_identity` | Forecasts；Radar；Today's Outlook | 0 |
| `global-homepage-032-canva-com` | https://canva.com/ → https://www.canva.com/ | `landing_page` | `page_identity` | Canva home；What will you design today?；Start designing；Canva AI | 0 |
| `global-homepage-034-news-yahoo-co-jp` | https://news.yahoo.co.jp/ → https://news.yahoo.co.jp/ | `content_list` | `named_link` | Yahoo!ニュース；トピックス；アクセスランキング | 8 |
| `global-homepage-035-fandom-com` | https://fandom.com/ → https://www.fandom.com/ | `content_list` | `named_link` | Fandom；GAMES；MOVIES；TV；EXPLORE PROPERTIES | 3 |
| `global-homepage-036-globo-com` | https://globo.com/ → https://www.globo.com/ | `content_list` | `named_link` | globo.com；Guia de Compras；Previsão do tempo | 17 |
| `global-homepage-037-mail-ru` | https://mail.ru/ → https://mail.ru/ | `content_list` | `named_link` | Mail.ru；Почта；Облако；Календарь | 8 |
| `global-homepage-038-samsung-com` | https://samsung.com/ → https://www.samsung.com/us/ | `landing_page` | `page_identity` | Samsung；Mobile；TV & AV；Appliances；Computers & Monitors | 0 |
| `global-homepage-039-duckduckgo-com` | https://duckduckgo.com/?kad=en_US&kl=us-en → https://duckduckgo.com/?kad=en_US&kl=us-en | `entry_page` | `page_identity` | DuckDuckGo；Search；Duck.ai；Settings；Privacy Policy | 0 |
| `global-homepage-041-discord-com` | https://discord.com/ → https://discord.com/ | `landing_page` | `page_identity` | GROUP CHAT THAT’S ALL FUN & GAMES；MAKE YOUR GROUP CHATS MORE FUN；STREAM LIKE YOU’RE IN THE SAME ROOM；HOP IN WHEN YOU'RE FREE, NO NEED TO CALL | 0 |
| `global-homepage-042-github-com` | https://github.com/ → https://github.com/ | `landing_page` | `page_identity` | GitHub features；GitHub Copilot；GitHub Actions；GitHub Codespaces | 0 |
| `global-homepage-044-spotify-com` | https://open.spotify.com/?locale=en_US → https://open.spotify.com/?locale=en_US | `entry_page` | `page_identity` | Your Library；Trending songs；Popular artists；Popular albums and singles | 0 |
| `global-homepage-045-nytimes-com` | https://nytimes.com/ → https://www.nytimes.com/ | `content_list` | `named_link` | The New York Times；TOP STORIES；NEWSLETTERS；PODCASTS | 95 |
| `global-homepage-046-zoom-us` | https://zoom.us/ → https://www.zoom.com/ | `landing_page` | `page_identity` | Find out what's possible when work connects；One platform. Endless ways to work together.；Trusted by millions. Built for you.；Businesses achieve more with Zoom | 0 |
| `global-homepage-047-booking-com` | https://www.booking.com/?lang=en-us → https://www.booking.com/?lang=en-us | `entry_page` | `page_identity` | Booking.com；Stays；Flights；Car rental；Search | 0 |
| `global-homepage-049-brave-com` | https://brave.com/ → https://brave.com/ | `landing_page` | `page_identity` | The browser that puts you first；Private search；Powerful VPN；A Web with fewer ads | 0 |
| `global-homepage-056-apple-com` | https://apple.com/ → https://www.apple.com/ | `landing_page` | `page_identity` | Apple；Mac；iPhone；Watch | 0 |
| `global-homepage-060-espn-com` | https://espn.com/ → https://www.espn.com/ | `content_list` | `named_link` | ESPN；Quick Links；Top Headlines | 40 |
| `global-homepage-062-bbc-com` | https://bbc.com/ → https://www.bbc.com/ | `content_list` | `named_link` | BBC；News；Sport；Business；Technology | 29 |
| `global-homepage-063-paypal-com` | https://paypal.com/ → https://www.paypal.com/us/home | `landing_page` | `page_identity` | PayPal；Personal；Business；Get the PayPal app | 0 |
| `global-homepage-065-ya-ru` | https://ya.ru/ → https://ya.ru/ | `entry_page` | `page_identity` | Запрос；Поиск по картинке；Сервисы | 0 |
| `global-homepage-067-deepseek-com` | https://www.deepseek.com/en/ → https://www.deepseek.com/en/ | `landing_page` | `page_identity` | Into the Unknown；DeepSeek Web；API Platform；API Docs | 0 |
| `global-homepage-068-indeed-com` | https://indeed.com/ → https://www.indeed.com/ | `entry_page` | `page_identity` | Your next job starts here；Trending Searches；Trending Jobs；Search | 0 |
| `global-homepage-072-rakuten-co-jp` | https://rakuten.co.jp/ → https://www.rakuten.co.jp/ | `product_catalog` | `named_link` | ようこそ楽天市場へ；楽天スーパーDEAL；人気商品ランキング；ジャンル | 9 |
| `global-homepage-075-telegram-org` | https://telegram.org/ → https://telegram.org/ | `landing_page` | `page_identity` | Why Telegram?；Simple；Private；Secure；Powerful | 0 |
| `global-homepage-080-cnn-com` | https://cnn.com/ → https://www.cnn.com/ | `content_list` | `named_link` | CNN；MORE TOP STORIES；CNN UNDERSCORED；Across our coverage | 96 |
| `global-homepage-081-namu-wiki` | https://namu.wiki/ → https://namu.wiki/w/%EB%82%98%EB%AC%B4%EC%9C%84%ED%82%A4:%EB%8C%80%EB%AC%B8 | `landing_page` | `page_identity` | 나무위키에 오신 것을 환영합니다!；나무위키는 누구나 기여할 수 있는 위키입니다.；검증되지 않았거나 편향된 내용이 있을 수 있습니다.；나무위키의 도움말 | 0 |
| `global-homepage-082-shein-com` | https://us.shein.com/ → https://us.shein.com/ | `landing_page` | `page_identity` | SHEIN；Women Clothing；Home & Living；Electronics | 0 |
| `global-homepage-084-qq-com` | https://qq.com/ → https://www.qq.com/ | `content_list` | `named_link` | 产品推荐；热点精选；腾讯新闻出品；热门问答 | 30 |
| `global-homepage-085-douyin-com` | https://douyin.com/ → https://www.douyin.com/jingxuan | `entry_page` | `page_identity` | 精选；推荐；放映厅；短剧 | 0 |
| `global-homepage-086-usps-com` | https://usps.com/ → https://www.usps.com/ | `landing_page` | `page_identity` | Quick Tools；Track a Package；Find USPS Locations；Buy Stamps | 0 |
| `global-homepage-087-instructure-com` | https://instructure.com/ → https://www.instructure.com/ | `landing_page` | `page_identity` | Instructure；Canvas；Explore Canvas；Why Instructure | 0 |
| `global-homepage-088-primevideo-com` | https://www.primevideo.com/-/zh/storefront → https://www.primevideo.com/-/zh/offers/nonprimehomepage?ref_=dv_web_force_root&language=zh | `landing_page` | `page_identity` | Prime Video；电影；电视节目；体育 | 0 |
| `global-homepage-090-rutube-ru` | https://rutube.ru/ → https://rutube.ru/ | `content_list` | `named_link` | RUTUBE；Для вас；Shorts；ТВ-каналы；Рекомендации | 64 |

## 排除记录

- `facebook.com` — `login_page` — 首页主体是账号登录/注册表单。
- `instagram.com` — `login_page` — 首页主体是账号登录/注册表单。
- `x.com` — `access_denied` — 首次加载返回 HTTP 403 且没有可见正文。
- `tiktok.com` — `capture_error` — 浏览器未能取得可审阅页面。
- `yandex.ru` — `duplicate_final_page` — 自然重定向到 dzen.ru，与保留案例完全重合。
- `linkedin.com` — `login_page` — 页面标题与主体均以登录或注册为中心。
- `bet.br` — `blank_or_error` — 最终进入浏览器错误页且可见正文为空。
- `pinterest.com` — `login_page` — 首次页面包含密码登录/注册界面；不把登录门后的内容作为首页正文。
- `live.com` — `capture_error` — 浏览器未能取得可审阅页面。
- `vk.com` — `login_page` — 首次页面主体是 VK 登录和注册入口。
- `claude.ai` — `login_page` — 自然重定向到 /login，主体是登录表单。
- `t.me` — `duplicate_final_page` — 自然重定向到 telegram.org，与保留案例完全重合。
- `ebay.com` — `generic_error` — HTTP 403 页面显示站点错误信息。
- `imdb.com` — `captcha_or_security_verification` — 页面为 Human Verification 安全检查。
- `cricbuzz.com` — `access_denied` — HTTP 403 Access Denied。
- `ozon.ru` — `generic_error` — HTTP 403，页面提示连接不可用。
- `aliexpress.com` — `login_page` — 自然重定向到登录页。
- `bbc.co.uk` — `duplicate_final_page` — 自然重定向到 bbc.com，与保留案例完全重合。
- `office.com` — `duplicate_final_page` — 自然重定向到 m365.cloud.microsoft，与 cloud.microsoft 保留案例重合。
- `docomo.ne.jp` — `not_found` — HTTP 404，正文仅为 Not Found。
- `amazon.in` — `regional_duplicate` — 与 amazon.com 属于高度重复的 Amazon 地区首页；主集透明去重。
- `amazon.co.jp` — `regional_duplicate` — 与 amazon.com 属于高度重复的 Amazon 地区首页；主集透明去重。
- `msn.com` — `blank` — HTTP 200 但渲染后可见正文长度为 0。
- `etsy.com` — `blank_or_blocked` — HTTP 403 且渲染后可见正文为空。
- `music.youtube.com` — `unavailable` — 页面明确显示该服务在当前地区不可用。
- `disneyplus.com` — `blank_or_error` — 最终进入浏览器错误页且可见正文为空。
- `amazon.de` — `regional_duplicate` — 与 amazon.com 属于高度重复的 Amazon 地区首页；主集透明去重。
- `adobe.com` — `blank_or_error` — 最终进入浏览器错误页且可见正文为空。
- `chat.deepseek.com` — `login_page` — 自然重定向到 /sign_in，主体是登录表单。
- `hbomax.com` — `access_denied` — HTTP 403 Access Denied。
- `zillow.com` — `captcha_or_security_verification` — HTTP 403，要求按住控件完成人机验证。
- `amazon.co.uk` — `regional_duplicate` — 与 amazon.com 属于高度重复的 Amazon 地区首页；主集透明去重。
- `ok.ru` — `login_page` — 首次页面主体是社交网络登录和注册入口。
- `wildberries.ru` — `captcha_or_security_verification` — HTTP 498，页面仅显示正在检查浏览器。
- `quora.com` — `login_page` — 首页主体是第三方登录、邮箱注册和密码登录表单。
- `temu.com` — `login_page` — 第二轮完整取证时自然重定向到 /login.html，页面标题为 Temu | Login，主体为登录/注册。
- `walmart.com` — `captcha_or_security_verification` — 第二轮完整取证出现 Robot or human? 安全检查，不能把其后的页面片段当作正常商品首页。
- `roblox.com` — `locale_contract_not_deterministic` — Independent observations of the same homepage differed between English and Chinese. The tested locale=en_us URL did not reliably switch the page body to English, and the current contract format does not express a whole-page locale bundle without mixing languages. The case is explicitly deferred rather than weakened or scored against an unstable language contract.

## 人工复核栏

- 规则文件：`research/global-homepage-rules.json`
- 测试集：`evaluation/global-homepages-dataset.json`
- 本次规则构建不发起供应商调用；已有历史调用不参与参考规则制定。
