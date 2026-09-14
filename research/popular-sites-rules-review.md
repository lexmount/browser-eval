# 全球热门网站 100 站规则冻结记录

- 总计 100 站 × 12 方法 = 1,200 条全新正式结果；旧 46/100 Lexmount Global 结果不复用、不覆盖。
- Samsung 固定到美国站；Elsevier 允许独立观察到的同语义中英文页面文字。
- Booking.com 与 Prime Video 因当前地区访问契约不可复现而移出新矩阵，由候补 Grok、Shopify 补足。
- 每案例每方法 1 次、45 秒截止；方法内串行、方法间并行，禁止为提高分数而重试。
- 报告继续使用仓库唯一的 `harness/web_fetch/report.py` 和 `scripts/build_report.py`。
- `named_link` 的稳定文字属于直接加载页面，不要求每条记录包含；每条计数记录必须独立具备有效标题和匹配主机、完整路径的 URL。

## 冻结清单

| 序号 | 案例 | 队列 | URL | 规则 |
|---:|---|---|---|---|
| 1 | `global-homepage-001-google-com` | `global_homepage` | https://www.google.com/?hl=en | `page_identity` |
| 2 | `global-homepage-002-youtube-com` | `global_homepage` | https://www.youtube.com/?hl=en&gl=US&persist_hl=1 | `page_identity` |
| 3 | `global-homepage-005-chatgpt-com` | `global_homepage` | https://chatgpt.com/ | `page_identity` |
| 4 | `global-homepage-006-reddit-com` | `global_homepage` | https://reddit.com/ | `named_link` |
| 5 | `global-homepage-008-whatsapp-com` | `global_homepage` | https://whatsapp.com/ | `page_identity` |
| 6 | `global-homepage-010-bing-com` | `global_homepage` | https://bing.com/ | `page_identity` |
| 7 | `global-homepage-011-wikipedia-org` | `global_homepage` | https://wikipedia.org/ | `page_identity` |
| 8 | `global-homepage-012-gemini-google-com` | `global_homepage` | https://gemini.google.com/ | `page_identity` |
| 9 | `global-homepage-013-yahoo-co-jp` | `global_homepage` | https://yahoo.co.jp/ | `named_link` |
| 10 | `global-homepage-015-yahoo-com` | `global_homepage` | https://www.yahoo.com/?p=us | `named_link` |
| 11 | `global-homepage-016-amazon-com` | `global_homepage` | https://amazon.com/ | `named_link` |
| 12 | `global-homepage-018-baidu-com` | `global_homepage` | https://baidu.com/ | `page_identity` |
| 13 | `global-homepage-020-naver-com` | `global_homepage` | https://naver.com/ | `named_link` |
| 14 | `global-homepage-021-netflix-com` | `global_homepage` | https://netflix.com/ | `page_identity` |
| 15 | `global-homepage-023-cloud-microsoft` | `global_homepage` | https://cloud.microsoft/ | `page_identity` |
| 16 | `global-homepage-025-bilibili-com` | `global_homepage` | https://bilibili.com/ | `named_link` |
| 17 | `global-homepage-027-dzen-ru` | `global_homepage` | https://dzen.ru/ | `named_link` |
| 18 | `global-homepage-028-microsoft-com` | `global_homepage` | https://microsoft.com/ | `page_identity` |
| 19 | `global-homepage-029-twitch-tv` | `global_homepage` | https://www.twitch.tv/?lang=en | `named_link` |
| 20 | `global-homepage-030-weather-com` | `global_homepage` | https://weather.com/ | `page_identity` |
| 21 | `global-homepage-032-canva-com` | `global_homepage` | https://canva.com/ | `page_identity` |
| 22 | `global-homepage-034-news-yahoo-co-jp` | `global_homepage` | https://news.yahoo.co.jp/ | `named_link` |
| 23 | `global-homepage-035-fandom-com` | `global_homepage` | https://fandom.com/ | `named_link` |
| 24 | `global-homepage-036-globo-com` | `global_homepage` | https://globo.com/ | `named_link` |
| 25 | `global-homepage-037-mail-ru` | `global_homepage` | https://mail.ru/ | `named_link` |
| 26 | `global-homepage-038-samsung-com` | `global_homepage` | https://www.samsung.com/us/ | `page_identity` |
| 27 | `global-homepage-039-duckduckgo-com` | `global_homepage` | https://duckduckgo.com/?kad=en_US&kl=us-en | `page_identity` |
| 28 | `global-homepage-041-discord-com` | `global_homepage` | https://discord.com/ | `page_identity` |
| 29 | `global-homepage-042-github-com` | `global_homepage` | https://github.com/ | `page_identity` |
| 30 | `global-homepage-044-spotify-com` | `global_homepage` | https://open.spotify.com/?locale=en_US | `page_identity` |
| 31 | `global-homepage-045-nytimes-com` | `global_homepage` | https://nytimes.com/ | `named_link` |
| 32 | `global-homepage-046-zoom-us` | `global_homepage` | https://zoom.us/ | `page_identity` |
| 33 | `global-homepage-049-brave-com` | `global_homepage` | https://brave.com/ | `page_identity` |
| 34 | `global-homepage-056-apple-com` | `global_homepage` | https://apple.com/ | `page_identity` |
| 35 | `global-homepage-060-espn-com` | `global_homepage` | https://espn.com/ | `named_link` |
| 36 | `global-homepage-062-bbc-com` | `global_homepage` | https://bbc.com/ | `named_link` |
| 37 | `global-homepage-063-paypal-com` | `global_homepage` | https://paypal.com/ | `page_identity` |
| 38 | `global-homepage-065-ya-ru` | `global_homepage` | https://ya.ru/ | `page_identity` |
| 39 | `global-homepage-067-deepseek-com` | `global_homepage` | https://www.deepseek.com/en/ | `page_identity` |
| 40 | `global-homepage-068-indeed-com` | `global_homepage` | https://indeed.com/ | `page_identity` |
| 41 | `global-homepage-072-rakuten-co-jp` | `global_homepage` | https://rakuten.co.jp/ | `named_link` |
| 42 | `global-homepage-075-telegram-org` | `global_homepage` | https://telegram.org/ | `page_identity` |
| 43 | `global-homepage-080-cnn-com` | `global_homepage` | https://cnn.com/ | `named_link` |
| 44 | `global-homepage-081-namu-wiki` | `global_homepage` | https://namu.wiki/ | `page_identity` |
| 45 | `global-homepage-082-shein-com` | `global_homepage` | https://us.shein.com/ | `page_identity` |
| 46 | `global-homepage-084-qq-com` | `global_homepage` | https://qq.com/ | `named_link` |
| 47 | `global-homepage-085-douyin-com` | `global_homepage` | https://douyin.com/ | `page_identity` |
| 48 | `global-homepage-086-usps-com` | `global_homepage` | https://usps.com/ | `page_identity` |
| 49 | `global-homepage-087-instructure-com` | `global_homepage` | https://instructure.com/ | `page_identity` |
| 50 | `global-homepage-090-rutube-ru` | `global_homepage` | https://rutube.ru/ | `named_link` |
| 51 | `challenge-01` | `legacy_popular_list` | https://www.anthropologie.com/collection-decorative-candles | `named_link` |
| 52 | `challenge-02` | `legacy_popular_list` | https://search.jd.com/Search?keyword=%E8%80%B3%E6%9C%BA&enc=utf-8 | `named_link` |
| 53 | `challenge-03` | `legacy_popular_list` | https://www.xiaohongshu.com/explore | `named_link` |
| 54 | `challenge-04` | `legacy_popular_list` | https://www.zhihu.com/hot | `named_link` |
| 55 | `challenge-05` | `legacy_popular_list` | https://movie.douban.com/top250 | `named_link` |
| 56 | `challenge-06` | `legacy_popular_list` | https://www.dianping.com/shanghai/ch10 | `named_link` |
| 57 | `challenge-09` | `legacy_popular_list` | https://www.imdb.com/chart/top/ | `named_link` |
| 58 | `challenge-10-justwatch` | `legacy_popular_list` | https://www.justwatch.com/us/tv-shows | `named_link` |
| 59 | `china-homepage-008-doubao-com` | `china_ranked_homepage` | https://doubao.com/ | `page_identity` |
| 60 | `china-homepage-012-feishu-cn` | `china_ranked_homepage` | https://feishu.cn/ | `page_identity` |
| 61 | `china-homepage-018-163-com` | `china_ranked_homepage` | https://163.com/ | `named_link` |
| 62 | `china-homepage-019-taobao-com` | `china_ranked_homepage` | https://taobao.com/ | `page_identity` |
| 63 | `china-homepage-026-aliyun-com` | `china_ranked_homepage` | https://aliyun.com/ | `page_identity` |
| 64 | `china-homepage-028-weibo-com` | `china_ranked_homepage` | https://weibo.com/ | `page_identity` |
| 65 | `china-homepage-029-360-com` | `china_ranked_homepage` | https://360.com/ | `page_identity` |
| 66 | `china-homepage-033-dingtalk-com` | `china_ranked_homepage` | https://dingtalk.com/ | `page_identity` |
| 67 | `china-homepage-036-hao123-com` | `china_ranked_homepage` | https://hao123.com/ | `page_identity` |
| 68 | `china-homepage-039-1688-com` | `china_ranked_homepage` | https://1688.com/ | `page_identity` |
| 69 | `china-homepage-040-quark-cn` | `china_ranked_homepage` | https://quark.cn/ | `page_identity` |
| 70 | `china-homepage-042-kdocs-cn` | `china_ranked_homepage` | https://kdocs.cn/ | `page_identity` |
| 71 | `china-homepage-044-kimi-com` | `china_ranked_homepage` | https://kimi.com/ | `page_identity` |
| 72 | `china-homepage-045-tmall-com` | `china_ranked_homepage` | https://tmall.com/ | `page_identity` |
| 73 | `china-homepage-046-wedoany-com` | `china_ranked_homepage` | https://wedoany.com/ | `named_link` |
| 74 | `china-homepage-047-qianwen-com` | `china_ranked_homepage` | https://qianwen.com/ | `page_identity` |
| 75 | `china-homepage-048-douyu-com` | `china_ranked_homepage` | https://douyu.com/ | `named_link` |
| 76 | `china-homepage-050-openai-com` | `china_ranked_homepage` | https://openai.com/ | `page_identity` |
| 77 | `china-homepage-052-alipay-com` | `china_ranked_homepage` | https://alipay.com/ | `page_identity` |
| 78 | `china-homepage-053-sohu-com` | `china_ranked_homepage` | https://sohu.com/ | `named_link` |
| 79 | `china-homepage-055-huya-com` | `china_ranked_homepage` | https://huya.com/ | `named_link` |
| 80 | `china-homepage-056-cnki-net` | `china_ranked_homepage` | https://cnki.net/ | `page_identity` |
| 81 | `china-homepage-057-nih-gov` | `china_ranked_homepage` | https://nih.gov/ | `page_identity` |
| 82 | `china-homepage-058-huaban-com` | `china_ranked_homepage` | https://huaban.com/ | `named_link` |
| 83 | `china-homepage-059-jianying-com` | `china_ranked_homepage` | https://jianying.com/ | `page_identity` |
| 84 | `china-homepage-060-eastmoney-com` | `china_ranked_homepage` | https://eastmoney.com/ | `named_link` |
| 85 | `china-homepage-063-sina-com-cn` | `china_ranked_homepage` | https://sina.com.cn/ | `named_link` |
| 86 | `china-homepage-064-gamer520-com` | `china_ranked_homepage` | https://gamer520.com/ | `named_link` |
| 87 | `china-homepage-065-lingxing-com` | `china_ranked_homepage` | https://lingxing.com/ | `page_identity` |
| 88 | `china-homepage-067-alibaba-com` | `china_ranked_homepage` | https://alibaba.com/ | `named_link` |
| 89 | `china-homepage-068-youdao-com` | `china_ranked_homepage` | https://youdao.com/ | `page_identity` |
| 90 | `china-homepage-069-3dmgame-com` | `china_ranked_homepage` | https://3dmgame.com/ | `named_link` |
| 91 | `china-homepage-071-2345-com` | `china_ranked_homepage` | https://2345.com/ | `named_link` |
| 92 | `china-homepage-072-cctv-com` | `china_ranked_homepage` | https://cctv.com/ | `named_link` |
| 93 | `china-homepage-073-so-com` | `china_ranked_homepage` | https://so.com/ | `page_identity` |
| 94 | `china-homepage-075-volcengine-com` | `china_ranked_homepage` | https://volcengine.com/ | `page_identity` |
| 95 | `china-homepage-078-nga-cn` | `china_ranked_homepage` | https://nga.cn/ | `page_identity` |
| 96 | `china-homepage-080-yandex-com` | `china_ranked_homepage` | https://yandex.com/ | `page_identity` |
| 97 | `china-homepage-081-elsevier-com` | `china_ranked_homepage` | https://www.elsevier.com/en-us | `page_identity` |
| 98 | `china-homepage-082-huawei-com` | `china_ranked_homepage` | https://huawei.com/ | `page_identity` |
| 99 | `china-homepage-083-grok-com` | `china_ranked_reserve_replacement` | https://grok.com/ | `page_identity` |
| 100 | `china-homepage-085-shopify-com` | `china_ranked_reserve_replacement` | https://www.shopify.com/ | `page_identity` |
