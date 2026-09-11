# 全球热门网站 100 站规则冻结记录

- 总计 100 站：52 条沿用当前正式结果，48 条需要新的 `lexmount-global` 调用。
- 新调用由 8 个旧热门列表案例和 40 个中国榜首页组成；Amazon、Reddit 因重复只保留首页案例。
- 旧 52 条 URL、契约和原始调用不变，不重复付费调用。
- 报告继续使用仓库唯一的 `harness/web_fetch/report.py` 和 `scripts/build_report.py`。
- `named_link` 的稳定文字属于直接加载页面，不要求每条记录包含；每条计数记录必须独立具备有效标题和匹配主机、完整路径的 URL。

## 冻结清单

| 序号 | 案例 | 队列 | URL | 规则 |
|---:|---|---|---|---|
| 1 | `global-homepage-001-google-com` | `inherited_global_homepage` | https://www.google.com/?hl=en | `page_identity` |
| 2 | `global-homepage-002-youtube-com` | `inherited_global_homepage` | https://www.youtube.com/?hl=en&gl=US&persist_hl=1 | `page_identity` |
| 3 | `global-homepage-005-chatgpt-com` | `inherited_global_homepage` | https://chatgpt.com/ | `page_identity` |
| 4 | `global-homepage-006-reddit-com` | `inherited_global_homepage` | https://reddit.com/ | `named_link` |
| 5 | `global-homepage-008-whatsapp-com` | `inherited_global_homepage` | https://whatsapp.com/ | `page_identity` |
| 6 | `global-homepage-010-bing-com` | `inherited_global_homepage` | https://bing.com/ | `page_identity` |
| 7 | `global-homepage-011-wikipedia-org` | `inherited_global_homepage` | https://wikipedia.org/ | `page_identity` |
| 8 | `global-homepage-012-gemini-google-com` | `inherited_global_homepage` | https://gemini.google.com/ | `page_identity` |
| 9 | `global-homepage-013-yahoo-co-jp` | `inherited_global_homepage` | https://yahoo.co.jp/ | `named_link` |
| 10 | `global-homepage-015-yahoo-com` | `inherited_global_homepage` | https://www.yahoo.com/?p=us | `named_link` |
| 11 | `global-homepage-016-amazon-com` | `inherited_global_homepage` | https://amazon.com/ | `named_link` |
| 12 | `global-homepage-018-baidu-com` | `inherited_global_homepage` | https://baidu.com/ | `page_identity` |
| 13 | `global-homepage-020-naver-com` | `inherited_global_homepage` | https://naver.com/ | `named_link` |
| 14 | `global-homepage-021-netflix-com` | `inherited_global_homepage` | https://netflix.com/ | `page_identity` |
| 15 | `global-homepage-023-cloud-microsoft` | `inherited_global_homepage` | https://cloud.microsoft/ | `page_identity` |
| 16 | `global-homepage-025-bilibili-com` | `inherited_global_homepage` | https://bilibili.com/ | `named_link` |
| 17 | `global-homepage-027-dzen-ru` | `inherited_global_homepage` | https://dzen.ru/ | `named_link` |
| 18 | `global-homepage-028-microsoft-com` | `inherited_global_homepage` | https://microsoft.com/ | `page_identity` |
| 19 | `global-homepage-029-twitch-tv` | `inherited_global_homepage` | https://www.twitch.tv/?lang=en | `named_link` |
| 20 | `global-homepage-030-weather-com` | `inherited_global_homepage` | https://weather.com/ | `page_identity` |
| 21 | `global-homepage-032-canva-com` | `inherited_global_homepage` | https://canva.com/ | `page_identity` |
| 22 | `global-homepage-034-news-yahoo-co-jp` | `inherited_global_homepage` | https://news.yahoo.co.jp/ | `named_link` |
| 23 | `global-homepage-035-fandom-com` | `inherited_global_homepage` | https://fandom.com/ | `named_link` |
| 24 | `global-homepage-036-globo-com` | `inherited_global_homepage` | https://globo.com/ | `named_link` |
| 25 | `global-homepage-037-mail-ru` | `inherited_global_homepage` | https://mail.ru/ | `named_link` |
| 26 | `global-homepage-038-samsung-com` | `inherited_global_homepage` | https://samsung.com/ | `page_identity` |
| 27 | `global-homepage-039-duckduckgo-com` | `inherited_global_homepage` | https://duckduckgo.com/?kad=en_US&kl=us-en | `page_identity` |
| 28 | `global-homepage-041-discord-com` | `inherited_global_homepage` | https://discord.com/ | `page_identity` |
| 29 | `global-homepage-042-github-com` | `inherited_global_homepage` | https://github.com/ | `page_identity` |
| 30 | `global-homepage-044-spotify-com` | `inherited_global_homepage` | https://open.spotify.com/?locale=en_US | `page_identity` |
| 31 | `global-homepage-045-nytimes-com` | `inherited_global_homepage` | https://nytimes.com/ | `named_link` |
| 32 | `global-homepage-046-zoom-us` | `inherited_global_homepage` | https://zoom.us/ | `page_identity` |
| 33 | `global-homepage-047-booking-com` | `inherited_global_homepage` | https://www.booking.com/?lang=en-us | `page_identity` |
| 34 | `global-homepage-049-brave-com` | `inherited_global_homepage` | https://brave.com/ | `page_identity` |
| 35 | `global-homepage-056-apple-com` | `inherited_global_homepage` | https://apple.com/ | `page_identity` |
| 36 | `global-homepage-060-espn-com` | `inherited_global_homepage` | https://espn.com/ | `named_link` |
| 37 | `global-homepage-062-bbc-com` | `inherited_global_homepage` | https://bbc.com/ | `named_link` |
| 38 | `global-homepage-063-paypal-com` | `inherited_global_homepage` | https://paypal.com/ | `page_identity` |
| 39 | `global-homepage-065-ya-ru` | `inherited_global_homepage` | https://ya.ru/ | `page_identity` |
| 40 | `global-homepage-067-deepseek-com` | `inherited_global_homepage` | https://www.deepseek.com/en/ | `page_identity` |
| 41 | `global-homepage-068-indeed-com` | `inherited_global_homepage` | https://indeed.com/ | `page_identity` |
| 42 | `global-homepage-072-rakuten-co-jp` | `inherited_global_homepage` | https://rakuten.co.jp/ | `named_link` |
| 43 | `global-homepage-075-telegram-org` | `inherited_global_homepage` | https://telegram.org/ | `page_identity` |
| 44 | `global-homepage-080-cnn-com` | `inherited_global_homepage` | https://cnn.com/ | `named_link` |
| 45 | `global-homepage-081-namu-wiki` | `inherited_global_homepage` | https://namu.wiki/ | `page_identity` |
| 46 | `global-homepage-082-shein-com` | `inherited_global_homepage` | https://us.shein.com/ | `page_identity` |
| 47 | `global-homepage-084-qq-com` | `inherited_global_homepage` | https://qq.com/ | `named_link` |
| 48 | `global-homepage-085-douyin-com` | `inherited_global_homepage` | https://douyin.com/ | `page_identity` |
| 49 | `global-homepage-086-usps-com` | `inherited_global_homepage` | https://usps.com/ | `page_identity` |
| 50 | `global-homepage-087-instructure-com` | `inherited_global_homepage` | https://instructure.com/ | `page_identity` |
| 51 | `global-homepage-088-primevideo-com` | `inherited_global_homepage` | https://www.primevideo.com/-/zh/storefront | `page_identity` |
| 52 | `global-homepage-090-rutube-ru` | `inherited_global_homepage` | https://rutube.ru/ | `named_link` |
| 53 | `challenge-01` | `new_legacy_popular_list` | https://www.anthropologie.com/collection-decorative-candles | `named_link` |
| 54 | `challenge-02` | `new_legacy_popular_list` | https://search.jd.com/Search?keyword=%E8%80%B3%E6%9C%BA&enc=utf-8 | `named_link` |
| 55 | `challenge-03` | `new_legacy_popular_list` | https://www.xiaohongshu.com/explore | `named_link` |
| 56 | `challenge-04` | `new_legacy_popular_list` | https://www.zhihu.com/hot | `named_link` |
| 57 | `challenge-05` | `new_legacy_popular_list` | https://movie.douban.com/top250 | `named_link` |
| 58 | `challenge-06` | `new_legacy_popular_list` | https://www.dianping.com/shanghai/ch10 | `named_link` |
| 59 | `challenge-09` | `new_legacy_popular_list` | https://www.imdb.com/chart/top/ | `named_link` |
| 60 | `challenge-10-justwatch` | `new_legacy_popular_list` | https://www.justwatch.com/us/tv-shows | `named_link` |
| 61 | `china-homepage-008-doubao-com` | `new_china_ranked_homepage` | https://doubao.com/ | `page_identity` |
| 62 | `china-homepage-012-feishu-cn` | `new_china_ranked_homepage` | https://feishu.cn/ | `page_identity` |
| 63 | `china-homepage-018-163-com` | `new_china_ranked_homepage` | https://163.com/ | `named_link` |
| 64 | `china-homepage-019-taobao-com` | `new_china_ranked_homepage` | https://taobao.com/ | `page_identity` |
| 65 | `china-homepage-026-aliyun-com` | `new_china_ranked_homepage` | https://aliyun.com/ | `page_identity` |
| 66 | `china-homepage-028-weibo-com` | `new_china_ranked_homepage` | https://weibo.com/ | `page_identity` |
| 67 | `china-homepage-029-360-com` | `new_china_ranked_homepage` | https://360.com/ | `page_identity` |
| 68 | `china-homepage-033-dingtalk-com` | `new_china_ranked_homepage` | https://dingtalk.com/ | `page_identity` |
| 69 | `china-homepage-036-hao123-com` | `new_china_ranked_homepage` | https://hao123.com/ | `page_identity` |
| 70 | `china-homepage-039-1688-com` | `new_china_ranked_homepage` | https://1688.com/ | `page_identity` |
| 71 | `china-homepage-040-quark-cn` | `new_china_ranked_homepage` | https://quark.cn/ | `page_identity` |
| 72 | `china-homepage-042-kdocs-cn` | `new_china_ranked_homepage` | https://kdocs.cn/ | `page_identity` |
| 73 | `china-homepage-044-kimi-com` | `new_china_ranked_homepage` | https://kimi.com/ | `page_identity` |
| 74 | `china-homepage-045-tmall-com` | `new_china_ranked_homepage` | https://tmall.com/ | `page_identity` |
| 75 | `china-homepage-046-wedoany-com` | `new_china_ranked_homepage` | https://wedoany.com/ | `named_link` |
| 76 | `china-homepage-047-qianwen-com` | `new_china_ranked_homepage` | https://qianwen.com/ | `page_identity` |
| 77 | `china-homepage-048-douyu-com` | `new_china_ranked_homepage` | https://douyu.com/ | `named_link` |
| 78 | `china-homepage-050-openai-com` | `new_china_ranked_homepage` | https://openai.com/ | `page_identity` |
| 79 | `china-homepage-052-alipay-com` | `new_china_ranked_homepage` | https://alipay.com/ | `page_identity` |
| 80 | `china-homepage-053-sohu-com` | `new_china_ranked_homepage` | https://sohu.com/ | `named_link` |
| 81 | `china-homepage-055-huya-com` | `new_china_ranked_homepage` | https://huya.com/ | `named_link` |
| 82 | `china-homepage-056-cnki-net` | `new_china_ranked_homepage` | https://cnki.net/ | `page_identity` |
| 83 | `china-homepage-057-nih-gov` | `new_china_ranked_homepage` | https://nih.gov/ | `page_identity` |
| 84 | `china-homepage-058-huaban-com` | `new_china_ranked_homepage` | https://huaban.com/ | `named_link` |
| 85 | `china-homepage-059-jianying-com` | `new_china_ranked_homepage` | https://jianying.com/ | `page_identity` |
| 86 | `china-homepage-060-eastmoney-com` | `new_china_ranked_homepage` | https://eastmoney.com/ | `named_link` |
| 87 | `china-homepage-063-sina-com-cn` | `new_china_ranked_homepage` | https://sina.com.cn/ | `named_link` |
| 88 | `china-homepage-064-gamer520-com` | `new_china_ranked_homepage` | https://gamer520.com/ | `named_link` |
| 89 | `china-homepage-065-lingxing-com` | `new_china_ranked_homepage` | https://lingxing.com/ | `page_identity` |
| 90 | `china-homepage-067-alibaba-com` | `new_china_ranked_homepage` | https://alibaba.com/ | `named_link` |
| 91 | `china-homepage-068-youdao-com` | `new_china_ranked_homepage` | https://youdao.com/ | `page_identity` |
| 92 | `china-homepage-069-3dmgame-com` | `new_china_ranked_homepage` | https://3dmgame.com/ | `named_link` |
| 93 | `china-homepage-071-2345-com` | `new_china_ranked_homepage` | https://2345.com/ | `named_link` |
| 94 | `china-homepage-072-cctv-com` | `new_china_ranked_homepage` | https://cctv.com/ | `named_link` |
| 95 | `china-homepage-073-so-com` | `new_china_ranked_homepage` | https://so.com/ | `page_identity` |
| 96 | `china-homepage-075-volcengine-com` | `new_china_ranked_homepage` | https://volcengine.com/ | `page_identity` |
| 97 | `china-homepage-078-nga-cn` | `new_china_ranked_homepage` | https://nga.cn/ | `page_identity` |
| 98 | `china-homepage-080-yandex-com` | `new_china_ranked_homepage` | https://yandex.com/ | `page_identity` |
| 99 | `china-homepage-081-elsevier-com` | `new_china_ranked_homepage` | https://elsevier.com/ | `page_identity` |
| 100 | `china-homepage-082-huawei-com` | `new_china_ranked_homepage` | https://huawei.com/ | `page_identity` |
