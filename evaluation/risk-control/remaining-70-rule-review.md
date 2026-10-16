# 历史访问受限网址第 31—100 例规则审阅稿

> 状态：**已完成独立审核**。与[前 30 例规则](first-30-rule-review.md)共同构成当前 100 例标准答案；未使用候补。审核范围、修订和验证结果见[独立审核记录](rule-independent-audit.md)。
>
> 依据：正式名单绑定的保存正文、原始 HTML、页面结构和取证记录。独立审核中的网页现状可达性检查不用于建立规则；没有调用 Moli、没有运行产品测试，也没有用点击、展开、翻页或滚动触发新加载补充标准答案。

## 阅读方法

- 稳定文字列出的每一项均为必需项；没有证据支持的文案变体不配置。
- 变化列表不固定观察时的成员、名次、价格、日期或热度。有效记录必须满足同条目字段或 name-link、路径、去重和数量要求；门槛为 `ceil(N × 80%)`。
- 文章、文档、固定说明页及其他固定文本目标，都使用首次自动加载后直接显示或已经加载的完整目标正文。正文分区只划定全文的连续范围，不能只取开头、少数段落或任意挑选的章节；每区至少 60%，至少 `max(1, 分区数−1)` 区达到 80%。
- 属于本例目标范围的变化列表、新闻、版本或价格等不适合冻结成固定正文时，也不能直接略过，须改用 name-link、同条目字段、去重和数量规则覆盖。导航、页脚、广告、推荐、评论、图片文字、隐藏模板和链接后的内容不补入正文。
- 每例的参考文本、哈希、最终网址和语言以[精简 cohort manifest](qualification/cohort/cohort-manifest.json)为准。

---

## 第 31—50 例

## 31. risk-diverse-074｜Ticketmaster concerts

[打开目标网页](https://www.ticketmaster.com/discover/concerts)

- **网址、语言与目标：** 输入和最终网址相同；英文；演唱会发现页及首次加载的演出卡片，不固定当期演出、日期或排序。
- **稳定文字：** `Concert Tickets`、`All Concerts`、`Concerts Happening This Week`、`Get tickets to popular concerts before they're gone`；`Hotels`、`Sell`、`Gift Cards`、`Help`、`VIP`、`Ticketmaster Home page`、`Concerts`、`Sports`、`Arts, Theater & Comedy`、`Family`、`Cities`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **name-link：** 每条需有同一卡片的演出名称和真实详情链接。允许 `www.ticketmaster.com` 的 `/.../event/<页面实际ID>` 或 `/event/<页面实际ID>`；保存证据中的实际 ID 由字母、数字、下划线或连字符组成。也允许页面真实提供的 `www.ticketweb.com/event/...`；不能从名称或 ID 拼接链接。
- **数量与字段：** 绑定 HTML 有 34 个不同的事件形态链接，其中 `BLUE FRIDAY SPECIAL! Entry to All Shows at the Blue Moon for 2026!` 和 `I'm On the List! + 1 (Entry to all shows at the Blue Moon in 2026...)` 是全年入场资格产品，不是具体演唱会。排除这两条后 N=32、T=26，按规范化详情链接去重。同一卡片还需有日期或时间、场馆及城市/地区；具体值可变化。
- **正文：** 不采用固定演出正文；变化卡片由 name-link、同卡片字段、去重和数量共同覆盖。
- **排除：** 上述全年入场资格产品，以及艺人、场馆、分类、旅游推广、广告和导航链接不计为演出记录。

## 32. risk-diverse-101｜Azur Lane Wiki

[打开目标网页](https://azurlane.koumakan.jp/)

- **网址、语言与目标：** 根网址自动跳转到同站 `/wiki/Azur_Lane_Wiki`；英文；Wiki 首页身份及直接显示的项目介绍和资源栏目。
- **稳定文字：** `Commander, welcome to the`、`Azur Lane English Wiki`、`Beginner Resources`、`Gameplay Resources`、`Fleet Information`、`Weigh Anchor`。欢迎句在保存正文中分成普通文字、链接文字和感叹号，不再合成一个稳定文字；`Main page`、`Recent changes`、`Recent uploads`、`To do`、`Special pages`、`Help`、`Read`、`View source`、`View history`、`Appearance`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 三区覆盖欢迎语和游戏/发行方介绍、Beginner Resources 区、Gameplay Resources 与 Fleet Information 区的固定说明和入口名称。
- **排除：** 当前活动、新闻日期、页面统计和最后编辑时间。无变化列表数量规则。

## 33. risk-diverse-104｜Sanctum Development

[打开目标网页](https://dev.sanctum.geek.nz/)

- **网址、语言与目标：** 根网址自动跳转到同站 `/cgit`；英文；代码站及仓库目录。
- **稳定文字：** `Tom Ryder—Code`、`Name`、`Description`、`Owner`、`Idle`，以及分类标题 `Dotfiles`、`Experiments`、`Forks`；`index`、`about`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **name-link：** 每条仓库名称必须与同一行的真实仓库根链接对应；主机精确为 `dev.sanctum.geek.nz`，路径完整匹配 `/cgit/<仓库名>.git/`。`summary`、`log`、`tree` 等子页不能另算仓库。
- **数量与字段：** N=87，T=70，按仓库根路径去重；同一行还需有非空描述和 owner。`Idle` 值可变化。
- **正文：** 不冻结当前仓库名单或 idle 时间。
- **排除：** `summary`、`log`、`tree` 等仓库子页、分类标题、导航和页脚不计作仓库记录。

## 34. risk-diverse-106｜TYPO3 Extension Repository

[打开目标网页](https://extensions.typo3.org/)

- **网址、语言与目标：** 输入和最终网址相同；英文；扩展仓库首页及首次加载的扩展卡片。
- **稳定文字：** `Extension Repository`、`Browse Extensions`、`Search`、`Sort by`、`Filters`、`Works with TYPO3`、`Installation method`、`Publishing channel`、`Distribution`、`Extension tags`。后两项与其他筛选项同属固定筛选结构，不是当前扩展卡片内容；`About Extension Repository`、`What are extensions?`、`Extension Keys`、`TER Packagist Integration`、`FAQ`、`Publish an extension`、`REST API`、`Composer Support`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **name-link：** 名称与同一卡片真实链接对应；主机精确为 `extensions.typo3.org`，路径完整匹配 `/extension/<扩展键>` 或 `/package/<vendor>/<package>`，不能自行拼接。
- **数量与字段：** N=20，T=16，按规范化详情路径去重；同一卡片需有显示名称或扩展键、说明以及版本/兼容性信息。点赞、发布日期可变化。
- **正文：** 不采用固定扩展卡片正文；变化记录由 name-link、同卡片字段、去重和数量覆盖。
- **排除：** 筛选项、标签、分页和导航不计为扩展记录。

## 35. risk-diverse-113｜MozillaZine

[打开目标网页](https://mozillazine.org/)

- **网址、语言与目标：** 输入和最终网址相同；英文；归档状态说明及仍开放的社区入口。
- **稳定文字：** `MozillaZine's news site has gone into hibernation.`、`Forums`、`Firefox Support`、`Thunderbird Support`、`Seamonkey Support`、`Extensions and Themes`、`Knowledge Base`。
- **正文参考：** 两区覆盖休眠公告和社区仍开放说明，以及关于 archives 的完整说明。
- **排除：** 页脚、版权年份和链接后的论坛内容。

## 36. risk-diverse-114｜Pluralpedia

[打开目标网页](https://pluralpedia.org/)

- **网址、语言与目标：** 根网址跳转到 `/w/Main_Page`；英文；词典 Wiki 首页介绍和主要入门栏目。
- **稳定文字：** `From Pluralpedia, the collaborative plurality dictionary`、`Where To Start`、`What's plurality?`、`Want to Contribute?`；`View source`、`View history`、`Main Page`、`Discussion`、`Recent changes`、`Random page`、`Search`、`Appearance`。Main page 与 Main Page 只差大小写，评分归一化后是同一项，因此不重复登记。 这些文字均来自绑定的首次加载证据，仅用于页面外围覆盖诊断，不影响任务通过；它们不并入目标正文，排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 三区覆盖项目欢迎/用途、plurality 与术语入口说明、贡献与分类说明。
- **排除：** 最后编辑时间、当前条目数量及页脚。

## 37. risk-diverse-117｜RPM Fusion

[打开目标网页](https://rpmfusion.org/)

- **网址、语言与目标：** 输入和最终网址相同；英文；项目身份、用途和面向用户/打包者的入口说明。
- **稳定文字：** `RPM Fusion provides software that the Fedora Project or Red Hat doesn't want to ship`、`Our goal is to simplify end-user experience`、`For users`、`For packagers`、`Browse available packages`；`RPM Fusion`、`Configuration`、`ReportingBugs`、`Howto`、`FAQ`、`Contribute`、`Edit`、`Info`、`Other`、`Login`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 三区覆盖项目用途介绍、用户安装/使用入口、打包者和包浏览入口的固定说明。
- **排除：** 当前 Fedora/EL 版本号、具体仓库矩阵和更新时间；响应式导航中的 `Search` 不参与通过判定。

## 38. risk-diverse-118｜SquirrelJME

[打开目标网页](https://squirreljme.cc/)

- **网址、语言与目标：** 自动跳转到 `multiphasicapps.net/doc/ckout/readme.mkd`，页面项目名和指回原站的入口证明仍是 SquirrelJME 英文项目主页；覆盖首次加载的完整项目说明页。
- **稳定文字：** `SquirrelJME`、`Information`、`User Guide`、`Developer Resources`、`Repository`、`Links`、`Goals`、`Donations`、`Webring Button`、`Important Statements`；`Home`、`Download`、`JavaDoc`、`Notes`、`Timeline`、`Branches`、`Tags`、`Forum`、`Tickets`、`Wiki`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 从 Java ME 8 虚拟机用途介绍到 Important Statements 末尾，分五区：①项目用途、兼容目标、运行库/抽象层和 NanoCoat；②版权、致谢、纪念文字、Install4J 与完整 Information 资源；③Repository 的 Fossil、Tor、Git 镜像和源码快照说明，以及 Links/支持渠道；④Goals 的全部目标说明与 Donations；⑤Webring Button 的使用说明和完整 Important Statements。代码和命令属于正文。
- **排除：** 页面生成耗时、Fossil 版本和版权年份。5 区均需 60%，至少 4 区需 80%。

## 39. risk-diverse-121｜Dolphin Emulator Forums

[打开目标网页](https://forums.dolphin-emu.org/)

- **网址、语言与目标：** 输入和最终网址相同；英文；论坛首页及直接显示的论坛分类。
- **稳定文字：** `Dolphin, the GameCube and Wii emulator - Forums`、`Dolphin Emulator Discussion and Support`、`Game Support`、`Game Modifications`、`Dolphin Site`；`Login`、`Register`、`Dolphin Forums`、`Home`、`FAQ`、`Download`、`Wiki`、`Code`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **name-link：** 分类名称与同一区块的真实链接对应；主机为 `forums.dolphin-emu.org`，路径完整匹配 `/Forum-<实际slug>`。
- **数量与字段：** N=24，T=20，按完整分类路径去重；每条还需有同一区块的分类说明。主题数、帖子数和最后帖子可变化。
- **正文：** 不另设固定论坛列表正文；论坛分类由 name-link、同区块说明、去重和数量覆盖。
- **排除：** 最新帖子链接、用户链接、页脚和导航。

## 40. risk-diverse-122｜Free Pascal Wiki

[打开目标网页](https://wiki.freepascal.org/)

- **网址、语言与目标：** 输入和最终网址相同；英文；Wiki 首页及主要项目资源。
- **稳定文字：** `Welcome to the Free Pascal and Lazarus Wiki`、`Free Pascal`、`Lazarus`、`Official Website`、`Downloads`、`Community Participation`、`Tutorials`、`Books and Magazines`、`Roadmap`、`Related Sites`、`Wiki Portals`、`In the News`、`Projects using Free Pascal and Lazarus`、`Bug Tracker`；`Main Page`、`Read`、`View source`、`History`、`Documentation`、`FAQ`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 从欢迎说明到 Bug Tracker 末尾，分五区：①Wiki 用途、文档和新用户帮助；②Free Pascal 的官网、下载、社区、教程和书刊资源；③Lazarus 的官网、Roadmap、下载、社区、教程和书刊资源；④Related Sites 与完整 Wiki Portals；⑤In the News、Projects using Free Pascal and Lazarus 与 Bug Tracker。每区包含全部说明和固定入口。
- **排除：** 语言列表、当前版本、最后编辑时间和页脚。5 区均需 60%，至少 4 区需 80%。

## 41. risk-diverse-123｜Koha Community Wiki

[打开目标网页](https://wiki.koha-community.org/)

- **网址、语言与目标：** 自动规范到同站 `/wiki/Main_Page`；英文；Koha 官方社区 Wiki 首页。
- **稳定文字：** `Welcome to the official wiki of Koha.`、`Getting Involved`、`Categories`、`Koha Community Sites (and their status)`；`Main Page`、`Read`、`View source`、`View history`、`Search`、`Recent changes`、`Log in`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 三区覆盖 Koha 作为开源集成图书馆系统的介绍与历史、参与入口和类别说明、官方社区站点及其说明。
- **排除：** 当前状态值、最后编辑时间及页脚。

## 42. risk-diverse-129｜HydroOJ FAQs

[打开目标网页](https://ws.hydrooj.com/d/faqs/)

- **网址、语言与目标：** 输入和最终网址相同；中文；FAQ 文档区说明、首次加载的讨论和最近题目。HTTP 默认路线是验证页，正文依据绑定的无交互浏览器证据。
- **稳定文字：** `点击这里返回主站`、`本处为 Hydro 用户文档区域。`、`由于旧的文档位于 Github`、`请善用题库上方的搜索框进行搜索。`、`Discussion`、`Recent Problems`、`Discussion Nodes`；`Home`、`Problem Set`、`Record`、`Login`、`Sign Up`、`Language`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **讨论 name-link：** 标题与同一记录真实链接对应；主机为 `ws.hydrooj.com`，路径完整匹配 `/d/faqs/discuss/<十六进制ID>`，片段可保留但不作为身份。N=20，T=16。
- **题目 name-link：** 名称与同一记录真实链接对应；路径完整匹配 `/d/faqs/p/<题号>`。N=10，T=8。
- **正文：** 不冻结当前讨论标题或题目名称；两个变化区域分别由 name-link、路径、去重和数量覆盖。
- **排除：** 两个区域分别计数，不能互相补足；不固定当前讨论和题目名单，不进入详情页。

## 43. risk-diverse-134｜Zillow Homes for Sale

[打开目标网页](https://www.zillow.com/seattle-wa/)

- **网址、语言与目标：** 输入和最终网址相同；英文；Seattle 售房搜索及首次加载房源。精确地区属于任务身份，避免无地区入口按访问环境落到任意城市或空结果。
- **稳定文字：** `For sale`、`Price`、`Beds & baths`、`Property type`、`Filters`、`Save search`、`Seattle WA Real Estate & Homes For Sale`；`Submit Search`、`Search`、`Updates`、`Favorites`、`Plan`、`Inbox`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **name-link：** 地址/名称与同一卡片的真实链接对应；主机为 `www.zillow.com`，路径完整匹配 `/homedetails/<slug>/<数字>_zpid/`。
- **数量与字段：** N=9，T=8，按 zpid 去重；同一卡片需有页面可见的价格、卧室数、浴室数和面积，具体值可变化。房屋类型只存在于嵌入状态而未显示在首次加载卡片上，不作为输出必需字段。
- **正文：** 不采用固定房源正文；变化卡片由 name-link、同卡片字段、去重和数量覆盖。
- **排除：** `Loading`、结果总数、推荐、导航和页脚；其他地区的房源不能代替 Seattle 任务。

## 44. risk-diverse-135｜Go.Compare

[打开目标网页](https://www.gocompare.com/)

- **网址、语言与目标：** 输入和最终网址相同；英文；英国比较服务首页及直接显示的服务说明。
- **稳定文字：** `ABOUT GO.COMPARE`、`Go.Compare is a UK comparison site that launched in 2006.`、`WHY CHOOSE GO.COMPARE?`、`We’re free to use and completely independent.`；`Help & more info`、`Sign In`、`Car`、`Home`、`Travel`、`Business`、`Search`、`View all products`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 三区覆盖 About Go.Compare 的两段完整说明、Why choose 的三项说明、首页固定产品类别入口（Car、Home、Travel、Van、Energy、Motorbike、Broadband、Loans）。
- **排除：** 当期奖励金额、促销截止日、Trustpilot 分数/评论数、新闻和页脚产品长清单。

## 45. risk-diverse-138｜D Wiki

[打开目标网页](https://wiki.dlang.org/)

- **网址、语言与目标：** 根网址跳转到同站 `/The_D_Programming_Language`；英文；D 语言 Wiki 主页面。
- **稳定文字：** `The D Programming Language`、`Hello World`、`Introduction`、`Community`、`Contributing`、`Documentation`、`Compilers & Tools`、`Resources & Directory`、`Meta`；`Read`、`View source`、`View history`、`Main page`、`Community portal`、`Recent changes`、`Search`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 四区覆盖 D 语言定位和介绍、Hello World/入门与文档资源、社区/贡献/编译器工具说明、Resources & Directory 与 Meta 的完整固定入口。
- **排除：** 当前编译器版本、最后编辑时间和页脚。4 区均需 60%，至少 3 区需 80%。

## 46. risk-diverse-139｜DSource

[打开目标网页](https://dsource.org/)

- **网址、语言与目标：** 输入和最终网址相同；英文；已归档的 D 开源开发站首页，归档公告不是错误页。
- **稳定文字：** `Note: This website is archived.`、`dsource.org Open Source Development for the D Programming Language`、`Welcome to dsource.org`、`Getting Started`、`Other Helpful Sites`；`Home`、`Projects`、`Forums`、`Tutorials`、`Site`、`Wiki Navigation`、`Start Page`、`Index by Title`、`Index by Date`、`Last Change`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 三区覆盖归档公告和站点用途、Getting Started 的固定步骤、Other Helpful Sites 的固定说明。
- **排除：** 版权年份和链接后的项目内容。

## 47. risk-diverse-153｜LZNOIP Training

[打开目标网页](https://www.lznoip.club/training/66358a5906b752f133d95f9f)

- **网址、语言与目标：** 输入和最终网址相同；中文；指定教材训练页以及初始已经展开的前五章。其余章节虽显示标题，但隐藏题目不计入。
- **稳定文字：** `教材《C++版信息学奥赛一本通编程启蒙》题单`、`登录以参加训练计划`（英文界面为 `Login to join training plan`）、`题目`（英文界面为 `Problem`）、`尝试`（英文界面为 `Tried`）、`AC`、`难度`（英文界面为 `Difficulty`）；`首页`（英文界面为 `Home`）、`题库`（英文界面为 `Problem Set`）、`训练`（英文界面为 `Training`）、`比赛`（英文界面为 `Contest`）、`评测记录`（英文界面为 `Record`）、`排名`（英文界面为 `Ranking`）、`登录`（英文界面为 `Login`）、`注册`（英文界面为 `Sign Up`）、`Language`。中文文字来自绑定的首次加载证据；英文等价文字由 2026-09-29 的独立浏览器可见页面核对。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **同条目字段：** 每道题必须在同一行具有题号和题名、尝试数、AC 数及难度；保存页面没有给这些可见题目提供普通详情链接，因此不制造链接要求。
- **数量与去重：** 前五章分别 4、5、6、5、6 题，共 N=26，T=21，按题号去重。
- **正文：** 不冻结题名或统计值；初始展开的前五章由同条目字段、题号去重和数量覆盖。
- **排除：** 第六章以后折叠的题目、按钮文字、登录要求和图片不计为题目内容。

- **导航语言依据：** 2026-10-08 独立 reviewer 通过 Language → English 核对上述八项导航；来源为 [PR #39 的实地复核评论](https://github.com/lexmount/browser-eval/pull/39#discussion_r4214573453)。等价写法不删除任何必需导航组。

## 48. risk-diverse-158｜Scribus

[打开目标网页](https://www.scribus.net/)

- **网址、语言与目标：** 输入和最终网址相同；英文；项目首页及直接显示的产品说明。
- **稳定文字：** `Scribus`、`Open Source Desktop Publishing`、`Scribus is the Go-To Libre Desktop Publishing Application.`、`Libre Desktop Publishing`、`Why Should I Use Scribus?`；`Contribute`、`Scribus Wiki`、`Bug Tracker`、`Privacy Policy`、`About`、`Quick Tour`、`Support`、`Download`、`News`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 三区覆盖 libre DTP 和控制权介绍、开放 XML 文件格式与自动化说明、色彩管理和可靠 PDF/印刷能力说明。
- **排除：** 当前版本、新闻、下载链接和版权年份。

## 49. risk-diverse-159｜HAL Open Science

[打开目标网页](https://hal.science/)

- **网址、语言与目标：** 输入和最终网址相同；英文；开放科学档案首页的固定服务说明。
- **稳定文字：** `Open and share the knowledge`、`An international scope`、`A common good for research`、`A large collaborative community`、`An archive, some services`、`Consult HAL by discipline`；`Search`、`Documentation`、`English (EN)`、`Sign in`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 四区覆盖开放共享宗旨、国际范围、公共科研资源/社区、档案和服务说明。
- **排除：** 当前文献数量、新闻、日期和动态学科内容。

## 50. risk-diverse-160｜Observation.org

[打开目标网页](https://observation.org/)

- **网址、语言与目标：** 输入和最终网址相同；英文；生物多样性平台首页及固定入门说明。
- **稳定文字：** `Together we discover more!`、`Getting started`、`Record birds, plants and more. Identify species with photos using image recognition.`、`Organise a project`、`Use our data`；`Donate`、`Explore`、`Observations`、`Surroundings`、`Species`、`Locations`、`Photos`、`Sounds`、`Users`、`Projects`、`BioBlitzes`、`About us`、`Help`、`Statistics`、`Terms of service`、`Code of conduct`、`Privacy statement`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 三区覆盖记录与识别、组织项目、使用数据三个固定说明卡片。
- **排除：** 最新照片、观察、稀有记录、新闻、计数和日期。

---

## 第 51—75 例

## 51. risk-diverse-170｜Linkpuff Git

[打开目标网页](https://git.linkpuff.me/)

- **网址、语言与目标：** 输入和最终网址相同；英文；Forgejo 首页及站点身份说明。
- **稳定文字：** `Luatea`、`A painless, self-hosted Git service`、`Easy to install`、`Cross-platform`、`Lightweight`、`Open Source`；`Explore`、`Help`、`Sign in`、`Powered by Forgejo`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 三区覆盖自托管 Git 服务介绍、安装与跨平台说明、轻量和开源说明。
- **排除：** Forgejo 当前版本、语言选择和页脚。

## 52. risk-diverse-177｜Egosoft Forum

[打开目标网页](https://forum.egosoft.com/)

- **网址、语言与目标：** 输入和最终网址相同；英文；论坛首页的 `English Forums` 区域，不以随后出现的德语及其他语言论坛补数量。
- **稳定文字：** `egosoft.com`、`Space simulation development`、`English Forums`、`X4: Foundations` 及其说明 `This forum is the ideal place for all discussion relating to X4.`；`Quick links`、`Unanswered topics`、`Active topics`、`Search`、`FAQ`、`X4 WIKI`、`Game Registration`、`Login`、`Register`、`Home`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **name-link：** 分类名称与同一区块的真实链接对应；主机为 `forum.egosoft.com`，路径为 `/viewforum.php`，查询参数 `f` 必须是页面实际提供的数字分类 ID；会话参数 `sid` 不作为身份。
- **数量与字段：** English Forums 下有 N=20 个实际分类，T=16；`English Forums` 分组标题本身不计记录。每条还需同一区块分类说明；主题数、帖子数和最后帖子可变化。
- **正文：** 不另设固定论坛列表正文；英文分类由 name-link、同区块说明、去重和数量覆盖。
- **排除：** 德语及其他语言分组、最新帖子、用户和版主链接。

## 53. risk-diverse-181｜Wikimedia XTools Article Info

[打开目标网页](https://xtools.wmcloud.org/articleinfo/en.wikipedia.org/Artificial_intelligence)

- **网址、语言与目标：** 输入和最终网址相同；英文；固定 `Artificial intelligence` 的 Article Info 报告。
- **稳定文字：** `Artificial intelligence`、`en.wikipedia.org`、`General statistics`、`Authorship`、`Top editors`、`Year counts`、`Month counts`、`Assessments`；`Edit Counter`、`Page History`、`History`、`Log`、`Pageviews`、`Documentation`、`Feedback`、`Report an issue`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **同条目字段：** 报告需显示 ID、Wikidata ID、Page size、Total edits、Editors、Assessment、Page watchers、Pageviews、First edit、Latest edit、Prose、References 等标签及非空值；固定身份值为页面 ID `1164` 与 Wikidata `Q11660`，其余统计值可变化。
- **name-link：** 不采用；这是一份固定记录报告，不把统计区入口当列表成员。
- **正文：** 不冻结会变化的统计值、日期或编辑者名单；固定报告身份和字段完整性由稳定文字及同条目字段覆盖。
- **排除：** 当前计数、日期和编辑者排名不冻结为固定答案。

## 54. risk-diverse-182｜SP Codex Wiki

[打开目标网页](https://spcodex.wiki/)

- **网址、语言与目标：** 输入和最终网址相同；英文；The Smashing Pumpkins Wiki 首页。
- **稳定文字：** `Welcome to SPCodex`、`The Smashing Pumpkins wiki`、`wiki that combines the autonomous growth of a wiki with the power and passion of the SP family.`、`Help us build the world's largest knowledge base for The Smashing Pumpkins and related acts!`、`Discography`、`More fan sites`。不再使用“欢迎文字”这种未给出原文的描述；`Home`、`Studio sessions`、`Tour history`、`Equipment`、`Personnel`、`Photos`、`Random song`、`Random album`、`Random show`、`Categories`、`Database`、`Pumpkin Patch`、`FAQ`、`Search`、`Read`、`View source`、`View history`、`Recent changes`、`Special pages`、`Appearance`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 两区覆盖项目身份、用途和邀请用户帮助建设的完整说明，以及 More fan sites 下各固定站点的用途说明。
- **排除：** Album of the week、Song of the day、Did you know、Today in history（包括其中的 Releases 和 Live shows）等轮换内容，当前文章/歌曲数量、Discography 当前条目名单、最后编辑时间和动态统计。2 区均需 60%，至少 1 区需 80%。

## 55. risk-diverse-185｜Lumiera API Documentation

[打开目标网页](https://lumiera.org/doxy/)

- **网址、语言与目标：** 输入和最终网址相同；英文；API 文档首页及介绍。
- **稳定文字：** `Lumiera API Documentation`、`Lumiera Documentation Structure`、`API Documentation`、`Layer and Subsystem`。不再使用“固定标题”这种未给出原文的描述；`Lumiera`、`»edit your freedom«`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定。
- **正文参考：** 使用从 `This section contains the API and reference documentation of the Lumiera code base.` 到 `the file level comments of relevant interfaces` 的全部首页说明，分三区覆盖 API 文档用途介绍、完整文档层级说明、Layer/Subsystem 和文件注释说明。
- **排除：** 生成时间、版本、索引导航和链接后的 API 页面。

## 56. risk-diverse-192｜ibiblio

[打开目标网页](https://ibiblio.org/)

- **网址、语言与目标：** 自动跳转到 `https://www.ibiblio.org/`；英文；公共数字档案首页及服务介绍。HTTP 默认路线曾触发限制，正文依据绑定的浏览器证据。
- **稳定文字：** `Explore`、`Share`、`Software`、`Projects`、`About ibiblio`，以及 `ibiblio.org is a collaboration of the School of Information and Library Science and Information Technology Services at the University of North Carolina at Chapel Hill.`；`Donate`、`About`、`Help`、`Blog`、`Contact`、`Privacy`、`Logos`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **name-link：** 首次加载的首页新闻列表按变化列表核对。绑定证据显示 3 条新闻，因此至少返回 3 条完整且不重复的记录；每条必须在同一记录内包含新闻名称、可访问的真实链接、日期和正文说明。链接域名必须是 `www.ibiblio.org`，路径必须符合 `/archive/年/月/文章短名/`；相对链接按最终首页 `https://www.ibiblio.org/` 补全，按完整文章路径去重。不能用日期链接重复计数，不能猜测或拼接文章地址。
- **正文参考：** 不另设固定正文。新闻名称、日期和正文会变化，不冻结某次名单或文章原文；但必须通过上述 name-link 数量、真实路径和同条目字段检查，不能只返回导航或页脚外壳。
- **排除：** 导航和页脚链接不计新闻数量；分页入口、正文中的外部引用链接、项目数量和访问量不计新闻记录。

## 57. risk-diverse-193｜Fedora Documentation

[打开目标网页](https://docs.fedoraproject.org/en-US/docs/)

- **网址、语言与目标：** 输入和最终网址相同；英文；Fedora 文档总入口。
- **稳定文字：** `User Documentation`、`Fedora Linux`、`Beginner's Guide`、`Fedora Server`、`Fedora CoreOS`、`Fedora IoT`、`Fedora Workstation`、`Quick Docs`、`Fedora Project & Community`。
- **正文参考：** 三区覆盖 Fedora 文档用途和 Linux 概览、各产品文档的固定说明、Project & Community 文档说明。
- **排除：** 构建时间、当前版本列表、语言列表和页脚。

## 58. risk-diverse-219｜QEMU Wiki

[打开目标网页](https://wiki.qemu.org/)

- **网址、语言与目标：** 根网址跳转到 `/Main_Page`；英文；QEMU Wiki 首页介绍。
- **稳定文字：** `QEMU is a generic and open source machine emulator and virtualizer.`、`Creating Accounts`；`Main Page`、`Discussion`、`Read`、`View source`、`View history`、`Documentation`、`Links`、`Submit a Patch`、`Mailing Lists`、`Testing QEMU`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 三区覆盖机器模拟器说明、虚拟化用途说明、项目来源/下载与 Wiki 账户说明。
- **排除：** 最后编辑时间、导航、页脚和链接后的文档。

## 59. risk-diverse-220｜MusicBrainz Wiki

[打开目标网页](https://wiki.musicbrainz.org/)

- **网址、语言与目标：** 根网址跳转到 `/Main_Page`；英文；MusicBrainz Wiki 首页。
- **稳定文字：** `Welcome to the MusicBrainz Wiki!`、`Introduction to MusicBrainz`、`Frequently Asked Questions`、`Guidelines`、`Community Projects`、`MusicBrainz Development`、`Updating the Wiki`；`Main Page`、`Discussion`、`Read`、`View source`、`View history`、`Appearance`、`Search`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 四区覆盖 Wiki/项目入门、FAQ 与指南说明、社区项目和开发入口说明、Updating the Wiki 的完整编辑及代码维护说明。
- **排除：** 最后编辑时间、当前项目动态和页脚。4 区均需 60%，至少 3 区需 80%。

## 60. risk-diverse-221｜SABRE Roads

[打开目标网页](https://www.sabre-roads.org.uk/)

- **网址、语言与目标：** 输入和最终网址相同；英文；英国与爱尔兰道路爱好者社群首页。
- **稳定文字：** `Welcome to SABRE - The Society for All British and Irish Road Enthusiasts`、`Discover - SABRE Maps`、`Digest - SABRE Wiki`、`Discuss - SABRE Forum`、`Image Galleries`、`Get involved!`、`Society`、`Contact SABRE & Help`。最后一项与其他栏目同为固定联系分区标题；`Home`、`Websites`、`Links`、`Help`、`Log In`、`Register`、`Donate`、`Social Media`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 三区覆盖社群身份和三大入口、Image Galleries 的用途说明、Society/Get involved/Contact 的固定说明。
- **排除：** `Photo of the Month`、最近论坛讨论、当前道路/Wiki 条目、地图、图片、活动和日期。

## 61. risk-diverse-222｜Wikibase World

[打开目标网页](https://wikibase.world/)

- **网址、语言与目标：** 根网址跳转到 `/wiki/Project:Home`；英文；Wikibase 实例目录社区首页，不是 MediaWiki/Wikibase 软件官网。
- **稳定文字：** `Wikibase World`、`Add your Wikibase!`、`Browse`、`Data model`；`Read`、`View source`、`View history`、`Search`、`Main page`、`Recent changes`、`Recent discussions`、`Random page`、`Forum`、`Help about MediaWiki`、`New Item`、`New Property`、`New Schema`、`All Properties`、`Query Service`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 三区覆盖目录用途介绍、可登记实例的条件、添加实例的步骤说明。
- **排除：** 当前实例名单、数量、最后编辑时间和页脚。

## 62. risk-diverse-223｜Noisebridge

[打开目标网页](https://www.noisebridge.net/)

- **网址、语言与目标：** 根网址跳转到 `/wiki`；英文；Noisebridge hackerspace 首页介绍。
- **稳定文字：** `Noisebridge is a fun space for sharing, creation, collaboration, research, development, mentoring, and of course, learning.`、`Noisebridge runs on donations.`、`Visit Noisebridge Hackerspace`、`Get Involved`、`Help Out`、`Events and Classes`、`Photos`、`Info`、`Get in Touch`、`Noisebridge Lore`。不再使用“hackerspace 身份句”这种未给出原文的描述；`About`、`Visit`、`Manual`、`Contact`、`Guilds`、`Stuff`、`Events`、`Projects`、`Meetings`、`Donate`、`Read`、`View source`、`View history`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 五区覆盖全部固定主页说明：①空间身份、捐赠和社区入口；②Visit、Get Involved、Help Out、无障碍与开放时间说明；③Events and Classes 的日历使用说明和参加规则；④Photos 与 Info 的完整说明；⑤Get in Touch 与 Noisebridge Lore。地址和开放时间保留在当次正文参考中，但不单独设为稳定文字。
- **排除：** 每周活动的当前名称、时间和说明，视频画面及需同意后才加载的内容、最后编辑时间和页脚。5 区均需 60%，至少 4 区需 80%。

## 63. risk-diverse-225｜Anton Paar Wiki

[打开目标网页](https://wiki.anton-paar.com/)

- **网址、语言与目标：** 正式输入为根网址；绑定证据在 `Accept-Language: en-US` 下跳转到 `/us-en/`；英文；同一 Anton Paar 科学知识 Wiki 首页。补查观察到其他浏览器语言环境可能转到 `/cn-cn/`，因此评分取证必须保持英文请求环境，不能把任意地区跳转当作目标正文。
- **稳定文字：** `Anton Paar Wiki`、`Anton Paar Wiki is a living, growing scientific platform dedicated to highlighting the science behind your measurement solution.`、`Are you ready for your Eureka moment?`、`Articles`、`Tables`、`Calculators`。不再使用“平台介绍句”这种未给出原文的描述；`Home`、`Products`、`Services & Support`、`Shop`、`About Us`、`Careers`、`Contact`、`Recommended`、`Density`、`Viscosity`、`Rheology`、`Microwave Digestion`、`Surface characterization`、`Particle characterization`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 单区覆盖从 `Anton Paar Wiki is a living, growing scientific platform` 到 `Are you ready for your Eureka moment?` 的完整平台用途介绍；Articles/Tables/Calculators 只有入口名称，没有独立资源说明，因此仍作为稳定文字核对，不重复充当正文。
- **排除：** Cookie 设置、地区列表、隐藏菜单、当前内容数量和页脚；响应式导航中的 `Resources` 不参与通过判定。

## 64. risk-diverse-226｜Team Fortress Wiki

[打开目标网页](https://wiki.teamfortress.com/wiki/Main_Page)

- **网址、语言与目标：** 输入和最终网址相同；英文；官方 Team Fortress Wiki 首页身份和栏目结构。
- **稳定文字：** `Official Team Fortress Wiki`、`the official resource for the Team Fortress series.`、`Scout`、`Soldier`、`Pyro`、`Demoman`、`Heavy`、`Engineer`、`Medic`、`Sniper`、`Spy`、`Featured article`、`Latest updates`。九个职业名称是固定导航，不再使用“class 导航标题”这种未给出原文的描述；`Main Page`、`Discussion`、`Read`、`View source`、`View history`、`Search`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 两区覆盖官方资源身份说明和稳定的角色/内容分类入口。
- **排除：** 精选文章正文、最新更新列表、趋势、计数和日期。

## 65. risk-diverse-011｜Threads：NASA 公开帖子

[打开目标网页](https://www.threads.com/@nasa/post/DdEwHNClPdc?hl=zh-cn)

- **网址、语言与目标：** 输入和最终网址相同；界面受 `hl=zh-cn` 固定为中文，原帖正文为英文；固定 NASA 的指定帖子。目标语言指帖子正文，不表示整页界面必须为英文；自动出现的登录层不改变帖子身份。
- **稳定文字：** 作者 `nasa`、`Space Threads`，以及原帖开头 `Curly-haired crew, this one’s for you!`；`串文`、`翻译`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定。
- **正文参考：** 单区包含原帖直接加载的三句完整英文正文，从 `Curly-haired crew` 到 `Watch this video to see how she cares for her curls in space!`，需达到 80%。
- **排除：** 视频画面/字幕、回复、互动计数、日期、相关串文和登录层文案。

## 66. risk-diverse-012｜Goodreads：84 Charing Cross Road

[打开目标网页](https://www.goodreads.com/book/show/953716.84_Charing_Cross_Road)

- **网址、语言与目标：** 输入和最终网址相同；英文；指定图书的书名、作者、初始直接显示的简介和版本基础信息。
- **稳定文字：** `84, Charing Cross Road`、`Helene Hanff`、`A heartwarming love story about people who love books for readers who love books`；`Home`、`My Books`、`Browse`、`Community`、`Sign in`、`Want to Read`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 单区使用从 `This funny, poignant, classic love story unfolds` 到 `Anthony Hopkins and Anne Bancroft.` 的完整可见简介，需达到 80%。
- **同条目字段：** `97 pages, Hardcover` 与 `First published January 1, 1970` 用于指定版本基础信息；评分和评论数不固定。
- **排除：** `Show more` 后的折叠简介、作者长传、相似书籍、社区评论、价格和登录层。

## 67. risk-diverse-241｜POWER-Z 测试仪购买方式

[打开目标网页](https://www.power-z.cn/blogs/新闻/chargerlab-power-z-测试仪购买方式)

- **网址、语言与目标：** 输入经正常编码后仍是同一中文文章；使用首次加载出的整篇文章正文，不能只取 KM/KT 区别摘要。
- **稳定文字：** `ChargerLAB POWER-Z 测试仪购买方式`、`POWER-Z.cn`、`KM系列`、`主要区别`、`店长推荐`、`KT系列`、`重大事件`；`商品`、`下载`、`APP`、`PowerPi`、`THG Start`、`English`、`新闻`、`联系我们`、`关于我们`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 从文章首段 `POWER-Z是ChargerLAB推出的便携式电源测试仪` 到文章内“联系我们”信息结束，分五个连续区域：①项目介绍和官方购买渠道；②KM 系列总述、区别、推荐及 KM002C；③KM001C 与 KM001Pro；④KT 系列总述、区别、推荐、KT002 与 KT001；⑤加群说明、重大事件和文章内联系信息。每区包含边界内全部正文，包括正文中的价格和真实购买/说明链接文字。
- **排除：** 顶部产品导航、购物车、评论表单、文章后的站点页脚和站点级“关于我们”。5 区均需 60%，至少 4 区需 80%；响应式导航中的 `Cart`、`Home` 不参与通过判定。

## 68. risk-diverse-242｜TIB English homepage

[打开目标网页](https://www.tib.eu/en/)

- **网址、语言与目标：** 输入和最终网址相同；英文；TIB 英语首页的检索、机构介绍和服务栏目。正文依据绑定的无交互浏览器证据。
- **稳定文字：** `SEARCH FOR SPECIALIST LITERATURE AND INFORMATION`、`TIB Services`、`About TIB`、`Topics and Services`、`PROFILE OF TIB`；`CONTACT`、`CAREERS`、`PRESS`、`DEUTSCH`、`LOGIN`、`ORDER`、`SEARCH`、`LEARNING`、`PUBLISHING`、`ABOUT US`、`RESEARCH AT TIB`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 三区覆盖检索入口说明、About TIB 的完整机构职能/研究介绍、Topics and Services 的固定栏目名称。
- **排除：** 新闻、活动、开放时间、年度报告年份和联系方式的即时状态。

## 69. risk-diverse-244｜Sciencemadness Wiki

[打开目标网页](https://www.sciencemadness.org/smwiki/index.php/Main_Page)

- **网址、语言与目标：** 输入和最终网址相同；英文；化学 Wiki 首页介绍和编辑说明。
- **稳定文字：** `Welcome to the Sciencemadness Wiki`、`Dear Editors:`、`Editing guidelines`、`How to infobox`；`Read`、`View source`、`View history`、`Search`、`Main page`、`Recent changes`、`Random page`、`Help`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 三区覆盖 home chemists 资料库介绍、技术状态/欢迎说明、Dear Editors 和 infobox 的完整直接显示说明。
- **排除：** 最后编辑时间、导航、页脚和化学条目链接后的内容。

## 70. risk-diverse-245｜About xeno-canto

[打开目标网页](https://xeno-canto.org/about/xeno-canto)

- **网址、语言与目标：** 输入和最终网址相同；英文；About xeno-canto 整篇文章。
- **稳定文字：** `About xeno-canto`、`Sharing wildlife sounds from around the world`、`Aims`、`Principles`、`Contributing`、`Xeno-canto foundation`、`Terms of use`、`History`；`Advanced Search`、`Tips`、`Collection Details`、`API`、`Meet the Members`、`Frequently Asked Questions`、`Explore`、`By Region`、`By Taxonomy`、`Recent Recordings`、`Random Recordings`、`Upload Sounds`、`Forum`、`Mysteries`、`Articles`、`Log in / Register`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 使用从 `www.xeno-canto.org ("Xeno-canto", "XC") is a website` 到 History 末尾 `their number is now above 8,800. Awesome.` 的整篇正文，分五区：①About 与 Aims；②Principles；③Contributing 及三个 XC needs 小节；④Foundation 与 Terms of use；⑤完整 History。历史数字也保留在参考中，但不得仅靠数字命中。
- **排除：** 顶部导航、语言列表和站点页脚。5 区均需 60%，至少 4 区需 80%。

## 71. risk-diverse-248｜The Global European

[打开目标网页](https://theglobaleuropean.com/)

- **网址、语言与目标：** 输入和最终网址相同；英文；新闻聚合首页的固定栏目结构和首次加载条目，不固定新闻成员或观点。
- **稳定文字：** `AGP Picks`、`AGP Executive Report`、`Top headlines, summarized`、`AGP Wire`、`Perspectify News`、`Learn about labels.`、`Press Releases`；`Menu`、`Share`、`About Labels`、`About`、`Explore more AGPs`、`Got News to Share?`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **name-link：** AGP Picks 条目使用页面实际提供的 HTTP(S) 外站链接，并要求同一条目有标题和真实链接；保存证据没有为该栏逐条显示独立来源字段，因此不额外要求来源。Perspectify 条目使用页面实际提供的 HTTP(S) 外站链接，并要求同一卡片标题和来源；AGP Wire 链接主机为 `theglobaleuropean.com`、路径匹配 `/agp-article/<数字ID>-<slug>`；Press Releases 路径匹配 `/article/<数字ID>-<slug>`。不得根据标题拼接。
- **数量与字段：** 四区分别计数：AGP Picks N=10/T=8，只要求标题与真实链接；AGP Wire N=4/T=4，要求时间、来源和摘要；Perspectify N=8/T=7，要求时间、标签和来源；Press Releases N=6/T=5，要求日期、来源和摘要。按规范化链接去重；具体成员和值可变化。
- **正文：** 不冻结新闻标题、摘要或执行报告的当期内容；四个变化区域分别由 name-link、同卡片字段、去重和数量覆盖。
- **排除：** 桌面/移动重复的 Picks 只算一次；执行报告的动态摘要不充当新闻条目；四区不能互相补数量。

## 72. risk-diverse-262｜Hacklab public wiki

[打开目标网页](https://knowledge.hacklab.to/wiki/)

- **网址、语言与目标：** 自动规范到 `/wiki/Main_Page`；英文；Hacklab.TO 公共 Wiki 首页。
- **稳定文字：** `Welcome to Hacklab.TO's Public Wiki!`、`Policies`、`Visiting Members`、`Member Projects`、`Infrastructure`、`Equipment List`；`Hacklab.TO Public Wiki`、`Search`、`Create account`、`Log in`、`Main Page`、`Discussion`、`Read`、`View source`、`View history`、`Tools`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 三区覆盖 Wiki 访问/设备说明、政策和成员入口、成员项目与基础设施/设备类别。
- **排除：** 最后编辑时间、设备详情页、隐藏数据和页脚。

## 73. risk-diverse-263｜freedesktop wiki

[打开目标网页](https://www.freedesktop.org/wiki/)

- **网址、语言与目标：** 输入和最终网址相同；英文；freedesktop.org 项目主页完整介绍区域。
- **稳定文字：** `Welcome to freedesktop.org`、`Software projects`、`Specifications`、`Infrastructure`、`Getting Involved`、`Code of Conduct`、`Privacy and personal information`、`Contact us`；`Edit`、`Page History`、`Repo Info`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 五区覆盖项目总述和 Software、Specifications、Infrastructure、Getting Involved/Code of Conduct、Privacy/Contact/Sponsors/Donations 的直接显示说明。
- **排除：** 最后编辑时间、站点编辑工具和链接后的项目页面。

## 74. risk-diverse-267｜openSUSE Wiki

[打开目标网页](https://en.opensuse.org/Main_Page)

- **网址、语言与目标：** 输入和最终网址相同；英文；openSUSE Wiki 首页的项目和发行版介绍。
- **稳定文字：** `Project`、`Distribution`、`MicroOS`、`Open Build Service`、`OpenQA`、`OSEM`、`YaST`、`Kiwi`、`Wiki`、`Popular Portals`。OSEM 与 Kiwi 和同区其他项目一样是固定项目入口，原规则不应只在正文范围中提到它们而遗漏稳定文字；`Navigation`、`Help`、`Tools`、`Main page`、`Discussion`、`View`、`View source`、`History`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 五区覆盖 Project、Distribution、MicroOS、Open Build Service/OpenQA/OSEM/YaST/Kiwi、Wiki 用途与固定门户分类。
- **排除：** 当前发行版号、访问量、热门支持文章名单、最后编辑时间和版权年份。

## 75. risk-diverse-268｜Alpine Linux Wiki

[打开目标网页](https://wiki.alpinelinux.org/wiki/Main_Page)

- **网址、语言与目标：** 最终网址仅增加 `__goaway_challenge` 参数，正文仍为同一英文 Main Page；挑战参数不作为页面身份。
- **稳定文字：** `Welcome to Alpine Linux Wiki!`、`Alpine Linux is a security-oriented, lightweight Linux distribution`、`How can we help you?`、`Installation`、`FAQ`、`Glossary`、`Tutorials and Howtos`、`Developer Documentation`、`Contribute`；`Alpine Linux`、`Search`、`Create account`、`Log in`、`Wiki`、`Help`、`Read`、`View source`、`View history`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 两区覆盖发行版用途介绍和帮助栏目名称/说明。
- **排除：** `Current releases` 的版本号、最后编辑时间、挑战参数值和页脚；响应式导航中的 `Main Site`、`Recent Changes`、`Site Index`、`Categories` 不参与通过判定。

---

## 第 76—100 例

## 76. risk-new-006｜Dribbble：TAE Web Design for Travel & Tourism

[打开目标网页](https://dribbble.com/shots/16508584-TAE-Web-Design-for-Travel-Tourism)

- **网址、语言与目标：** 输入和最终网址相同；英文；固定作品详情页的标题、作者和直接显示的设计说明。
- **稳定文字：** `TAE - Web Design for Travel & Tourism`、`Outcrowd`、`At Outcrowd, we're big fans of travel`；`Shots`、`Explore`、`Hire Talent`、`Get Hired`、`Community`、`Sign up`、`Log in`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 单区包含从 `At Outcrowd, we're big fans of travel` 到 `We are already looking forward to new adventures!` 的完整作品说明，需达到 80%。
- **排除：** Cookie 弹层、颜色代码、联系/社群 CTA、服务价格、评论、More by Outcrowd、标签和页脚。

## 77. risk-diverse-199｜About ScummVM

[打开目标网页](https://wiki.scummvm.org/index.php?title=About)

- **网址、语言与目标：** 输入和最终网址相同；英文；About ScummVM 整篇文章。
- **稳定文字：** `Brief description`、`A more thorough description`、`The inner workings of adventure and role-playing games`、`The scripting language of a game`、`Where does ScummVM fit in?`；`Search`、`Create account`、`Log in`、`Home`、`Supported platforms`、`User documentation`、`Read`、`View source`、`View history`、`Privacy policy`、`Disclaimers`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 从 `ScummVM is a program which allows you to run certain classic adventure and role-playing games` 到文章末尾 disadvantages 的 `...and perhaps more.`，分五区：①Brief description；②游戏资源和内部工作方式；③脚本语言及示例；④ScummVM 定位和优点；⑤缺点。每区使用边界内全部正文。
- **排除：** 导航、语言列表、Retrieved from、最后编辑时间和页脚。5 区均需 60%，至少 4 区需 80%。

## 78. risk-diverse-200｜OpenWrt quick start

[打开目标网页](https://openwrt.org/docs/guide-quick-start/start)

- **网址、语言与目标：** 输入和最终网址相同；英文；快速安装指南当前页面的全部直接显示内容，不进入文章列表中的子页。
- **稳定文字：** `Quick start guide for OpenWrt installation`、`The following preparation is recommended, before flashing OpenWrt firmware:`、`Quick start guide article list`；`OpenWrt Wiki`、`Tools`、`Log In`、`Recent Changes`、`Media Manager`、`Sitemap`、`Show pagesource`、`Old revisions`、`Backlinks`、`Back to top`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 三区覆盖安装前完整准备事项、提问/求助与 factory install 指引、Quick start guide article list 的完整直接显示目录。
- **排除：** 左侧站点导航、翻译列表、最后修改时间、账户提示和页脚。

## 79. risk-diverse-201｜Arch Linux installation guide

[打开目标网页](https://wiki.archlinux.org/title/Installation_guide)

- **网址、语言与目标：** 输入和最终网址相同；英文；首次加载出的整篇安装指南，不进入链接后的专题页。
- **稳定文字：** `Installation guide`、`Pre-installation`、`Installation`、`Configure the system`、`Reboot`、`Post-installation`；`Home`、`Packages`、`Forums`、`Wiki`、`GitLab`、`Security`、`AUR`、`Download`、`Read`、`View source`、`View history`、`Appearance`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 使用文章导言到 Post-installation 结束的全文，分五区：①导言及 Pre-installation 从镜像获取到联网；②时钟、分区、格式化和挂载；③镜像选择和安装基本软件包；④Fstab、Chroot、Time、Localization；⑤Network configuration、Initramfs、Root password、Boot loader、Reboot、Post-installation。代码、警告和注释均属于正文。
- **排除：** 目录的重复标题、语言导航、Retrieved from、最后编辑时间和页脚。5 区均需 60%，至少 4 区需 80%；响应式导航中的 `Main page`、`Table of contents`、`Getting involved` 不参与通过判定。

## 80. risk-diverse-202｜Haiku Trac guide

[打开目标网页](https://dev.haiku-os.org/wiki/TracGuide)

- **网址、语言与目标：** 输入和最终网址相同；英文；Trac User and Administration Guide 当前页全部直接显示内容。
- **稳定文字：** `The Trac User and Administration Guide`、`Introduction`、`User Guide`、`Administrator Guide`、`Support and Other Sources of Information`；`Search`、`Login`、`Help/Guide`、`About Trac`、`Preferences`、`Register`、`Wiki`、`Timeline`、`Roadmap`、`Browse Source`、`View Tickets`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 五区覆盖 TracGuide 用途及 Introduction、Wiki/版本控制用户指南、Ticket/其他用户模块、完整 Administrator Guide、Support and Other Sources of Information。
- **排除：** 顶部目录重复项、最后修改时间、下载格式和产品页脚。

## 81. risk-diverse-204｜ReactOS overview article

[打开目标网页](https://reactos.org/wiki/ReactOS)

- **网址、语言与目标：** 输入和最终网址相同；英文；ReactOS 概览整篇文章。
- **稳定文字：** `About ReactOS`、`What Is ReactOS`、`Compatible`、`Secure`、`Lightweight`、`Open`、`Reliable`、`Portability`、`Extensible`、`Object Based`、`See Also`；`Read`、`View source`、`View history`、`Recent changes`、`Search`、`Back to Website`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 从 `ReactOS® is an effort to create a Free Software replacement` 到 See Also 最后一项，分五区：①About/What Is ReactOS；②Compatible 与 Secure；③Lightweight/Open/Reliable；④Portability 与 Extensible；⑤Object Based 和完整 See Also。每区包含完整段落和条目。
- **排除：** 语言列表、Retrieved from、最后编辑时间和页脚。5 区均需 60%，至少 4 区需 80%。

## 82. risk-diverse-205｜Superlove about

[打开目标网页](https://superlove.sayitditto.net/about)

- **网址、语言与目标：** 输入和最终网址相同；英文；About superlove 整篇正文。
- **稳定文字：** `About superlove`、`introduction`、`contact me (the admin)`、`credits`、`badges`、`link back buttons`；`Browse`、`Works`、`Bookmarks`、`Tags`、`Collections`、`Search`、`People`、`About`、`About Us`、`News`、`FAQ`、`Wrangling Guidelines`、`Donate or Volunteer`、`Site Status`、`Links`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 从 introduction 下 `hi i'm melo` 到 link back buttons 的贡献者说明，分四区：①完整 introduction；②管理员联系和邮件/PGP 说明；③完整 credits；④badges 与 link back buttons。
- **排除：** 登录框、站点分类导航、搜索提示和 Footer。4 区均需 60%，至少 3 区需 80%。

## 83. risk-diverse-208｜About Pluralpedia

[打开目标网页](https://pluralpedia.org/w/Project:About)

- **网址、语言与目标：** 输入在站内重定向到项目介绍页，仍是同一英文目标；使用整篇项目介绍。
- **稳定文字：** `Pluralpedia is a community project to create a dictionary of plural terms.`、`History`、`Principles`、`Inclusivity`、`Neutrality`、`Respecting the Community`、`Community Project`、`Hosting`；`View source`、`View history`、`Project page`、`Discussion`、`Main page`、`Recent changes`、`Random page`、`Search`、`Appearance`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 从开头 `This project page should only be edited by the staff team` 到 Hosting 末尾，分四区：①页面维护说明、mascot 说明、项目用途与 History；②Inclusivity/Neutrality；③Respecting the Community/Community Project；④Hosting 的完整说明。图片本身不参与匹配，但正文中的可见图片说明属于该区文字。
- **排除：** Retrieved from、最后编辑时间、导航和页脚。4 区均需 60%，至少 3 区需 80%。

## 84. risk-diverse-209｜RPM Fusion FAQ

[打开目标网页](https://rpmfusion.org/FAQ)

- **网址、语言与目标：** 输入和最终网址相同；英文；首次加载出的整篇 FAQ。
- **稳定文字：** `Frequently Asked Questions`、`What is RPM Fusion?`、`What packages are available from RPM Fusion?`、`How do I use RPM Fusion?`、`General`、`Packages`；`RPM Fusion`、`Configuration`、`ReportingBugs`、`Howto`、`FAQ`、`Contribute`、`Edit`、`Info`、`Other`、`Login`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 从 What is RPM Fusion 的回答到最后 `How can I list all installed RPM Fusion Packages` 的回答，分五区：①项目和四类仓库；②使用/配置/安装及 Fedora 不收录原因；③合法性、信任、支持版本/架构；④报错、贡献和 broken dependencies；⑤General 与 Packages 下的全部问答。
- **排除：** 顶部目录的重复问题、站点导航和页脚。5 区均需 60%，至少 4 区需 80%；响应式导航中的 `Search` 不参与通过判定。

## 85. risk-diverse-210｜Installing Free Pascal

[打开目标网页](https://wiki.freepascal.org/Installing_the_Free_Pascal_Compiler)

- **网址、语言与目标：** 输入和最终网址相同；英文；首次加载出的整篇安装文档。
- **稳定文字：** `Installing the Free Pascal Compiler`、`Overview`、`Linux`、`FreeBSD`、`OpenBSD`、`Windows`、`macOS`、`Testing the FPC Install`、`Installing cross compilers`、`See also`；`Main Page`、`Read`、`View source`、`History`、`Documentation`、`FAQ`、`Downloads`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 使用正文全部安装说明，分五区：①Overview、导言和一般安装选择；②Linux、FreeBSD、OpenBSD 的包、ports、tar 和源码安装；③Windows 安装器与源码安装；④macOS 官方包与源码安装；⑤Testing the FPC Install、Installing cross compilers 和完整 See also。
- **排除：** 顶部目录重复项、语言/导航、Retrieved from、最后编辑时间和页脚。5 区均需 60%，至少 4 区需 80%。

## 86. risk-diverse-211｜Koha getting involved

[打开目标网页](https://wiki.koha-community.org/wiki/Getting_involved)

- **网址、语言与目标：** 输入和最终网址相同；英文；Getting involved 整篇贡献指南。
- **稳定文字：** `Getting involved`、`Documenting & translating Koha: using your language skills for good`、`Improving our manual`、`Becoming a translator`、`Going on a bug hunt…`、`Testing bug fixes`、`Fixing bugs: submitting patches`；`Read`、`View source`、`View history`、`Search`、`Recent changes`、`Log in`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 从入门参与说明到 Developer handbook 链接表结束，分五区：①社区沟通与决策/入门；②文档改进与翻译；③报告 bug；④测试修复；⑤提交补丁和完整 Developer handbook 资源表。
- **排除：** 站点导航、Retrieved from、分类、最后编辑时间和页脚。5 区均需 60%，至少 4 区需 80%。

## 87. risk-diverse-212｜D language getting started

[打开目标网页](https://wiki.dlang.org/Getting_Started)

- **网址、语言与目标：** 输入和最终网址相同；英文；Getting Started 整篇文档。
- **稳定文字：** `Getting Started`、`Your first program`、`Choosing a compiler`、`Running D code like a script`、`Choose your code editor`、`Using the D language for your domain`、`Troubleshooting`、`Searching with search engines`；`Read`、`View source`、`View history`、`Main page`、`Community portal`、`Recent changes`、`Search`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 从 `So, when you have enough reasons to try D` 到 Specific search engine 末尾，分五区：①第一程序与选择编译器；②RDMD 全部说明和示例；③DUB 示例；④编辑器、领域使用、Going further、Troubleshooting；⑤搜索引擎的全部说明。
- **排除：** 顶部目录重复项、Retrieved from、最后编辑时间和导航。5 区均需 60%，至少 4 区需 80%。

## 88. risk-diverse-213｜About Scribus

[打开目标网页](https://www.scribus.net/about/)

- **网址、语言与目标：** 输入和最终网址相同；英文；About 页面整篇正文。
- **稳定文字：** `About`、`Scribus is a libre page layout program for almost every operating system`、`Underneath a user-friendly interface you will discover a powerful tool that provides much more than you probably expect.`、`Get in contact with the Team`、`Meet the team`；`Contribute`、`Scribus Wiki`、`Bug Tracker`、`Privacy Policy`、`Quick Tour`、`Support`、`Download`、`News`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 从 `Welcome to Scribus` 到 `Meet the team` 结束，分四区：①项目历史与定位；②界面、绘图、文件格式、文字系统和色彩能力；③XML、脚本和 PDF 能力；④Get in contact with the Team 的完整联系说明及随后直接显示的 Meet the team 标题。
- **排除：** 团队照片、成员图片说明和人员名单会随团队变化，不冻结；下载/贡献按钮、分享、伙伴和页脚排除。4 区均需 60%，至少 3 区需 80%。

## 89. risk-diverse-214｜About Observation

[打开目标网页](https://observation.org/pages/about/)

- **网址、语言与目标：** 输入和最终网址相同；英文；About Observation.org 整篇正文。
- **稳定文字：** `About Observation.org`、`Mission statement`、`Data and community`、`Technology`、`Organisation and partners`；`Donate`、`Explore`、`Observations`、`Surroundings`、`Species`、`Locations`、`Photos`、`Sounds`、`Users`、`Projects`、`BioBlitzes`、`About us`、`Help`、`Statistics`、`Terms of service`、`Code of conduct`、`Privacy statement`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 从平台建立和隐私法介绍到 Organisation and partners 末尾，分四区：①平台简介与 Mission；②Data and community；③Technology；④Organisation and partners。正文中的规模数字保留在参考中但不单独冻结为稳定文字。
- **排除：** 移动应用推广、伙伴列表、捐赠 CTA 和页脚。4 区均需 60%，至少 3 区需 80%。

## 90. risk-diverse-215｜About SPCodex

[打开目标网页](https://spcodex.wiki/SPCodex:About)

- **网址、语言与目标：** 输入和最终网址相同；英文；SPCodex About 整篇正文。
- **稳定文字：** `SPCodex is a central resource for all knowledge surrounding The Smashing Pumpkins`、`Backstory`、`Special thanks`、`Contact`、`Notes`；`Home`、`Discography`、`Studio sessions`、`Tour history`、`Equipment`、`Personnel`、`Photos`、`Random song`、`Random album`、`Random show`、`Categories`、`Database`、`Pumpkin Patch`、`FAQ`、`Search`、`Read`、`Edit source`、`View history`、`Recent changes`、`Special pages`、`Appearance`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 从 SPCodex 用途句到 Notes 结束，分四区：①用途和 Backstory；②完整 Special thanks；③Contact；④Notes。
- **排除：** 导航、Retrieved from、最后编辑时间、免责声明和页脚。4 区均需 60%，至少 3 区需 80%。

## 91. risk-diverse-216｜Fedora beginners guide

[打开目标网页](https://docs.fedoraproject.org/en-US/beginners-guide/)

- **网址、语言与目标：** 输入和最终网址相同；英文；当前直接显示的 Introduction 页面全部正文，不把侧栏中其他章节当成本页正文。
- **稳定文字：** `A Beginner's Guide to Fedora`、`Introduction`、`What is The Fedora Project?`、`Our Working Language`、`What is Fedora Linux?`；`In this section`、`Let’s Try Fedora!`、`Installing Fedora KDE Permanently`、`Fedora as your daily driver`、`Advanced KDE Use — "Release the Kraken!"`、`Advanced Command Line Use — "In the Beginning there was Shell"`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 从 `Welcome to Fedora, we’re glad to have you here!` 到 KDE Plasma 说明结束，分三区：①欢迎与指南用途；②Fedora Project 和工作语言；③Fedora Linux、用途、版本/桌面环境及 KDE Plasma 说明。
- **排除：** 左侧文档树、语言列表、最后更新时间/构建时间、贡献 CTA 和页脚。

## 92. risk-diverse-228｜Linux kernel README

[打开目标网页](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/about/)

- **网址、语言与目标：** 输入和最终网址相同；英文；Linux 内核仓库 About 页的完整 README。
- **稳定文字：** `Linux kernel`、`Quick Start`、`Essential Documentation`、`Who Are You?`、`For Specific Users`、`Communication and Support`；`kernel/git/torvalds/linux.git`、`Linux kernel source tree`、`Linus Torvalds`、`about`、`summary`、`refs`、`log`、`tree`、`commit`、`diff`、`stats`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 从 Linux kernel 用途介绍到 Communication and Support 末尾，分五区：①Quick Start/Essential Documentation/Who Are You；②New Kernel Developer 和 Academic Researcher；③Security Expert、Backport/Maintenance Engineer、System Administrator；④Maintainer、Hardware Vendor、Distribution Maintainer；⑤AI Coding Assistant 与 Communication and Support。
- **排除：** cgit 导航、生成时间和页脚。5 区均需 60%，至少 4 区需 80%。

## 93. risk-diverse-230｜Scioly getting started

[打开目标网页](https://scioly.org/wiki/Help:Getting_started)

- **网址、语言与目标：** 输入和最终网址相同；英文；Help:Getting started 整篇正文。
- **稳定文字：** `Welcome to the Scioly.org Wiki!`、`Creating an Account`、`Getting Around`、`Creating your Userpage and Making your First Edit`、`Next Steps`、`More Help`；`Forums`、`Wiki`、`Test Exchange`、`Gallery`、`Chat`、`Main Page`、`Recent changes`、`Random page`、`MediaWiki Help`、`Read`、`View source`、`View history`、`Appearance`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 从欢迎说明到 More Help 结束，分五区：①欢迎和创建账户；②Getting Around 全部入口说明；③创建 userpage 和首次编辑；④VisualEditor 工具及编辑操作；⑤Next Steps 和 More Help。
- **排除：** 顶部目录重复项、图片及替代文字、Retrieved from、最后编辑时间和页脚。5 区均需 60%，至少 4 区需 80%。

## 94. risk-diverse-231｜FreeCAD forum FAQ

[打开目标网页](https://forum.freecad.org/app.php/help/faq)

- **网址、语言与目标：** 输入和最终网址相同；英文；phpBB FAQ 首次加载出的全部问答正文。
- **稳定文字：** `FAQ`、`Frequently Asked Questions`、`Login and Registration Issues`、`User Preferences and settings`、`Posting Issues`、`Formatting and Topic Types`、`User Levels and Groups`、`Private Messaging`、`Friends and Foes`、`Searching the Forums`、`Subscriptions and Bookmarks`、`Attachments`、`phpBB Issues`；`FreeCAD Forum`、`The help and development forum of FreeCAD`、`Search`、`Advanced search`、`Quick links`、`Unanswered topics`、`Active topics`、`Forums`、`Login`、`Register`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 从 Login and Registration Issues 第一问到最后 `How do I contact a board administrator?` 的回答，分五区：①登录注册和用户设置；②发帖问题；③格式/主题类型和用户组；④私信、好友、搜索；⑤订阅、附件和 phpBB Issues。所有实际回答都属于参考，目录重复问题和反复出现的 `Top` 不计正文。
- **排除：** 站点导航、目录重复项和页脚。5 区均需 60%，至少 4 区需 80%。

## 95. risk-diverse-234｜What are TYPO3 extensions

[打开目标网页](https://extensions.typo3.org/about-extension-repository/what-are-extensions)

- **网址、语言与目标：** 输入和最终网址相同；英文；What are extensions 整篇说明文章。
- **稳定文字：** `What are extensions?`、`Extensions are the cornerstone in the internal architecture of TYPO3.`、`The Extension Manager`、`The structure`、`The Depth of the Deep`、`The TYPO3 Extension Repository`、`Technical Details`；`Extension Repository`、`Browse Extensions`、`About Extension Repository`、`Extension Keys`、`TER Packagist Integration`、`FAQ`、`Choosing an extension key`、`Publish an extension`、`REST API`、`Composer Support`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 从 cornerstone 定义到 Technical Details 末尾，分五区：①架构定位及 Easy sharing/Hassle-free updates；②Quick development、High quality、Documentation、Unlimited application、Developer responsibility 与 Support load；③完整 Extension Manager；④The structure 与 The Depth of the Deep；⑤The TYPO3 Extension Repository 与 Technical Details。每区包含边界内全部说明。
- **排除：** About Extension Repository 导航、FAQ 入口和页脚。5 区均需 60%，至少 4 区需 80%。

## 96. risk-diverse-236｜SquirrelJME user guide

[打开目标网页](https://multiphasicapps.net/doc/ckout/assets/user-guide/readme.mkd)

- **网址、语言与目标：** 输入和最终网址相同；英文；User Guide 当前页全部直接显示正文。
- **稳定文字：** `SquirrelJME User Guide`、`Hello! Thank you for trying SquirrelJME`、`Main Website:`、`Table of Contents`、`Frequently Asked Questions`、`List of errors`；`Home`、`Download`、`JavaDoc`、`Notes`、`Timeline`、`Branches`、`Tags`、`Forum`、`Tickets`、`Wiki`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 单区包含感谢/用途说明、主站和可查看位置、完整 Table of Contents，需达到 80%。
- **排除：** 顶部导航、生成耗时、Fossil 版本和版权年份。

## 97. risk-diverse-237｜Dolphin FAQ

[打开目标网页](https://dolphin-emu.org/docs/faq/)

- **网址、语言与目标：** 正式输入为无参数网址；绑定的 `Accept-Language: en-US` HTTP 证据保持同一网址并取得英文全文；Dolphin FAQ 首次加载出的全部问答正文。补查观察到其他浏览器语言环境可能转到 `/cn/`，因此评分取证必须保持英文请求环境；`?nocr=true` 只能作为人工核对同一英文目标的辅助入口，不能替换正式输入。
- **稳定文字：** `About Dolphin`、`Hardware and system requirements`、`Installation issues`、`Configuration issues`、`Common issues`、`Game issues`、`Dolphin development`、`Miscellaneous`；`Download`、`Blog`、`Screenshots`、`FAQ`、`Guides`、`Compatibility`、`Community`、`Forum`、`Wiki`、`GitHub`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 从 `What is Dolphin?` 到 Supported Dump Formats 最后一项，分五区：①About Dolphin 与许可证/开发者；②硬件和系统要求；③安装、配置和常见问题；④游戏问题和 Dolphin development；⑤Miscellaneous，包括核心数、性能、ROM/ISO 与支持格式。
- **排除：** 顶部目录重复项、语言列表、站点导航和页脚。5 区均需 60%，至少 4 区需 80%；响应式导航中的 `Discord` 不参与通过判定。

## 98. risk-diverse-238｜Egosoft forum FAQ

[打开目标网页](https://forum.egosoft.com/app.php/help/faq)

- **网址、语言与目标：** 输入和最终网址相同；英文；Egosoft phpBB FAQ 首次加载出的全部问答正文。
- **稳定文字：** `FAQ`、`Frequently Asked Questions`、`Login and Registration Issues`、`User Preferences and settings`、`Posting Issues`、`Formatting and Topic Types`、`User Levels and Groups`、`Private Messaging`、`Friends and Foes`、`Searching the Forums`、`Subscriptions and Bookmarks`、`Attachments`、`phpBB Issues`；`egosoft.com`、`Space simulation development`、`Search`、`Advanced search`、`Quick links`、`Unanswered topics`、`Active topics`、`X4 WIKI`、`Game Registration`、`Login`、`Register`、`Home`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 从 Login and Registration Issues 第一问到最后 `How do I contact a board administrator?` 的回答，按与第 94 例相同的五个连续主题区覆盖全部回答；目录重复问题和 `Top` 不计正文。
- **排除：** 论坛首页分类、站点导航、目录重复项和页脚。5 区均需 60%，至少 4 区需 80%。

## 99. risk-diverse-239｜About ibiblio

[打开目标网页](https://www.ibiblio.org/about/)

- **网址、语言与目标：** 输入和最终网址相同；英文；About ibiblio 整篇文章。
- **稳定文字：** `Welcome to ibiblio!`、`More Than Your Average Library`、`Who & Why`、`A pioneer from the start`、`Annual Reports`；`Explore`、`Share`、`Software`、`Projects`、`Donate`、`About`、`Help`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 从 `Home to one of the largest “collections of collections”` 到 Annual Reports 的年份列表结束，分五区：①欢迎与服务概览；②More Than Your Average Library 前半；③该节后半和开放协作模式；④Who & Why 及项目目标列表；⑤A pioneer from the start 和 Annual Reports。正文中出现的访问数字及年份保留在参考中，但不单独作为稳定文字。
- **排除：** 文章后的合作机构句和页脚导航。5 区均需 60%，至少 4 区需 80%。

## 100. risk-diverse-250｜Contributing to QEMU

[打开目标网页](https://wiki.qemu.org/Contribute)

- **网址、语言与目标：** 输入和最终网址相同；英文；Contribute 当前页全部正文。
- **稳定文字：** `Contribute`、`Project Infrastructure`、`Info for new Contributors`、`Student Outreach`、`Development`、`Subpages`；`Search`、`Create account`、`Log in`、`About QEMU`、`Download`、`License`、`Start Here`、`Report a Bug`、`Report a security issue`、`Submit a Patch`、`Mailing Lists`、`Testing QEMU`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 四区覆盖完整 Project Infrastructure、Info for new Contributors、Student Outreach 与 Development、Subpages 列表。
- **排除：** 顶部导航、目录重复项、Retrieved from、最后编辑时间和页脚。4 区均需 60%，至少 3 区需 80%。

---

## 实现表达上的已知事项

以下是规则本身已经确定、但当前公共评分结构未必能直接表达的组合；不能因此降低规则：

1. HydroOJ 需要 Discussion 与 Recent Problems 两区分别计数。
2. The Global European 需要四个列表区分别计数，不能用某一区的多余条目补另一缺失区。
3. Ticketmaster、Zillow 等卡片既要 name-link，也要检查同一卡片的必要字段。
4. Egosoft Forum 只计算 English Forums 下的 20 个分类，不能把其他语言分组并入。

这份文档只制定规则和记录证据判断；没有调用被测产品，也没有给出测试结果。
