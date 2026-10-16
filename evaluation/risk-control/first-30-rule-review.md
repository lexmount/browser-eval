# 历史访问受限网址前 30 例规则审阅稿

> 状态：**已完成独立审核**。与第 31—100 例规则共同构成当前 100 例标准答案；审核范围、修订和验证结果见[独立审核记录](rule-independent-audit.md)。
>
> 范围：正式名单第 1—30 例；未使用 10 个候补。
>
> 依据：交接文件、名单中绑定的保存正文和原始 HTML。独立审核中的网页现状可达性检查不用于建立规则；没有调用 Moli，也没有用点击、滚动或触发新加载补充标准答案。

逐例参考文本路径、SHA-256、最终网址和语言沿用[精简 cohort manifest](qualification/cohort/cohort-manifest.json)中的绑定，以案例 ID 查找即可；本文件不复制 30 组长哈希。第 1—8、10 例的保存观察来自 2026-09-21 批次，第 9、11—30 例来自 2026-09-24 批次。若本文件与该绑定不一致，应先视为规则稿错误，不能悄悄改用另一份网页材料。

## 阅读方法

每例都先固定输入网址、目标和语言，再列出实际采用的规则。稳定文字全部是必需项；没有必要的文案变体时不配置变体。变化列表不固定本次看到的成员名称，只检查真实名称与同条目链接、路径、去重和数量。数量门槛按 `ceil(独立观察有效条目数 × 80%)` 计算。

正文采用公共 v3 分区覆盖规则：每区至少 60%，至少 `max(1, 分区数−1)` 个区达到 80%；单区必须达到 80%。文章、文档、固定说明页及其他固定文本目标，都以首次自动加载后直接显示或已经加载的完整目标正文为参考，再将全文划分为最多 5 个连续区域；不能只取开头、少数段落或任意挑选的章节。下面列出的边界用于划分全文，不表示只保留边界附近的句子。属于本例目标范围的变化列表、新闻、版本或价格等不适合冻结成固定正文时，也不能直接略过，须改用 name-link、同条目字段、去重和数量规则覆盖。导航、页脚、广告、推荐、评论、折叠内容、图片文字和图片替代文字不计入正文。

| 序号 | 案例 | 主要规则 | 数量门槛 | 状态 |
|---:|---|---|---:|---|
| 1 | 知乎回答 | 身份＋稳定文字＋完整正文 5 区 | 不适用 | 已审核 |
| 2 | Flipkart 商品 | 身份＋稳定文字＋正文 5 区 | 不适用 | 已审核 |
| 3 | 微博热搜 | 稳定文字＋name-link | 51 中至少 41 | 已审核 |
| 4 | 今日头条文章 | 身份＋稳定文字＋整篇正文 5 区 | 不适用 | 已审核 |
| 5 | Dribbble 搜索 | 稳定文字＋name-link | 42 中至少 34 | 已审核 |
| 6 | Bilibili 热门 | 稳定文字＋name-link | 20 中至少 16 | 已审核 |
| 7 | 腾讯视频电影 | 稳定文字＋同卡片字段 | 12 中至少 10 | 已审核 |
| 8 | Sanctum About | 稳定文字＋完整说明正文 4 区 | 不适用 | 已审核替换 |
| 9 | Linux Kernel Git | 仓库身份＋稳定文字 | 不适用 | 已审核 |
| 10 | DebianInstall | 身份＋完整文档正文 5 区 | 不适用 | 已审核 |
| 11 | Wine Bugzilla | 身份＋稳定文字＋正文 3 区 | 不适用 | 已审核 |
| 12 | Scioly Wiki | 完整固定正文 5 区＋三个列表区 | B/C 各 23 中至少 19；州页 52 中至少 42 | 已审核 |
| 13 | Enlightenment Git | 仓库身份＋完整 README 5 区 | 不适用 | 已审核 |
| 14 | FreeCAD Forum | 稳定文字＋name-link | 52 中至少 42 | 已审核 |
| 15 | ScummVM Wiki | 身份＋稳定文字＋正文 3 区 | 不适用 | 已审核 |
| 16 | OpenWrt Wiki | 身份＋完整中央内容 5 区 | 不适用 | 已审核 |
| 17 | ArchWiki | 身份＋24 项分布式稳定文字＋完整正文 3 区 | 不适用 | 已审核 |
| 18 | FreeBSD SVNweb | 稳定文字＋name-link | 11 中至少 9 | 已审核 |
| 19 | Purism Phosh | 仓库身份＋稳定文字＋正文 2 区 | 不适用 | 已审核 |
| 20 | Haiku Trac | 身份＋稳定文字＋正文 3 区 | 不适用 | 已审核 |
| 21 | Devuan installer-iso | 仓库身份＋完整 README 5 区 | 不适用 | 已审核 |
| 22 | CFA Archive | 完整固定正文 4 区＋新闻 name-link | 3 中至少 3 | 已审核 |
| 23 | Proxmox Bugzilla | 身份＋稳定界面文字 | 不适用 | 已审核 |
| 24 | ReactOS Wiki | 身份＋完整固定正文 4 区 | 不适用 | 已审核 |
| 25 | Arch Linux 32 Packages | 稳定文字＋name-link | 100 中至少 80 | 已审核 |
| 26 | Slackware Git | 仓库身份＋稳定文字 | 不适用 | 已审核 |
| 27 | Duke Digital Repository | 身份＋稳定文字＋正文 3 区 | 不适用 | 已审核 |
| 28 | hebis Discovery | 身份＋稳定文字＋正文 2 区 | 不适用 | 已审核 |
| 29 | Xe Iaso | 完整固定正文 4 区＋文章 name-link | 8 中至少 7 | 已审核 |
| 30 | Superlove | 完整固定正文 5 区＋新闻 name-link | 3 中至少 3 | 已审核 |

---

## 1. risk-diverse-002｜知乎：人工智能应用场景回答

[打开目标网页](https://www.zhihu.com/question/282715644/answer/3629347314)

- **网址、语言与目标：** 输入和观察最终网址相同；中文；只取指定问题下 Pulsar 的该条回答，不含其他回答与推荐。
- **稳定文字（全部必需）：** `人工智能那么火~如今AI的应用场景都有哪些？`、`Pulsar`、`一、AI的对外赋能潜力：技术不会孤立发展`、`二、「AI+5G」促进AI对外赋能更多场景`、`城市生活：低成本高效率的垃圾分类宣导`、`工业生产：可弹性部署的AI视觉检测`、`三、AI大模型取得突破：更广泛的落地场景`、`物联水利大模型：做出更及时准确的水情判断`、`食品生产场景：全天候监督，给群众更强安全感`、`大模型应用：提高基建，促进落地`。问题、作者和全文各主要部分均有独立定位点，不能只取得回答开头；`推荐`、`热榜`、`专栏`、`圈子`、`直答`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **name-link：** 不采用。这是固定回答正文，不以变化条目列表计分。
- **正文参考：** 使用独立保存的首次页面文档中已经完整载入的指定回答，从 `这是一个好问题。` 到末段 `成为推动本世纪人类社会发展的主要技术。`，不要求点击“阅读全文”，也不使用 Moli 输出补标准答案。全文分五个连续区域：①开头三段及 AI 对生产生活的概括；②第一部分“AI的对外赋能潜力”的全部正文；③第二部分“AI+5G”的城市生活、工业生产和小结；④第三部分“AI大模型取得突破”的水利与食品生产场景；⑤“大模型应用：提高基建”及全文结尾。每区采用边界内全部正文。
- **排除：** 图片、图片替代文字、图片说明 `copilot工具`、赞同/评论/分享数字、广告、其他回答和推荐不纳入。虽然页面用视觉高度折叠回答，但完整回答已在独立保存的首次页面文档中，无需点击或追加请求即可取得，因此不得再只用前三段作为答案。正文 5 区均需 60%，至少 4 区需 80%。

## 2. risk-001｜Flipkart：Acer Aspire Lite 商品详情

[打开目标网页](https://www.flipkart.com/acer-aspire-lite-amd-ryzen-3-quad-core-5400u-8-gb-256-gb-ssd-windows-11-home-al15-41-thin-light-laptop/p/itm1bc0bcb4598e7)

- **网址、语言与目标：** 输入和观察最终网址相同；英文；固定该商品页面实际显示的商品名称及已加载 `Product highlights`。网址中的旧式 slug 与页面显示名称不一致，因此身份以固定 URL 的实际页面名称和正文共同判断，不能按 slug 猜商品。
- **稳定文字：** 完整商品名 `Acer Aspire Lite Intel Core i3 11th Gen 1115G4 - (16 GB/512 GB SSD/Windows 11 Home) AL15-51 Thin and Light Laptop`；`Product highlights`；`Home`、`Computers`、`Laptops`、`Cart`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **name-link：** 不采用。商品 ID 只用于固定输入页，不能据此拼接任何商品链接。
- **正文参考：** 五区为 `Intel Core i3 11th Gen / Efficient performance for everyday use.`、`16 GB | 512 GB / Smooth gaming and video editing experience`、`15.6 Inch | Full HD Display / Clear and detailed visuals.`、`Intel Integrated / Efficient graphics for everyday use`、`1.59 Kg / Lightweight and easy to carry`。
- **排除：** 价格、库存、送达时间、评价数、推荐商品和需要点 `more` 才显示的内容。5 区均需 60%，至少 4 区需 80%。

## 3. risk-006｜微博热搜

[打开目标网页](https://s.weibo.com/top/summary)

- **网址、语言与目标：** 输入和观察最终网址相同；中文；主热搜榜及其分类，不固定当天成员、名次或热度。
- **稳定文字：** `微博热搜`、`我的`、`热搜`、`文娱`、`社会`、`科技`、`生活`、`体育`、`ACG`。这些文字必须出现在非榜单记录区域，防止某条热搜标题碰巧包含同词。
- **name-link：** 每条需有可见热搜名称和同一行实际提供的链接。协议为 HTTP/HTTPS，主机精确为 `s.weibo.com`，路径精确为 `/weibo`，查询参数 `q` 必须存在且非空；`Refer`、`t`、`band_rank` 可变化且不作为内容身份。相对链接只按本页基址解析。
- **数量与去重：** 独立 HTML 中主榜有 1 条置顶记录和 50 条编号记录，共 N=51，最低 T=41。按规范化后的 `q` 内容身份去重；`意见反馈`、页脚、导航与广告不计数。
- **正文：** 不采用固定正文快照；这是变化榜单。
- **排除：** `意见反馈`、页脚、导航、广告，以及名次和热度数字不计作热搜记录。

## 4. risk-011｜今日头条：关于人工智能，我们了解多少

[打开目标网页](https://www.toutiao.com/article/7595028554912399898/)

- **网址、语言与目标：** 固定输入为上面的无参数网址；观察最终页为同一路径并自动增加 `wid`。`wid` 不改变文章身份，可忽略其具体值；中文；取标题、来源、导语和首次自动加载后已经显示或加载的整篇文章正文。
- **稳定文字：** `关于人工智能，我们了解多少`；`中国网信杂志`；导语开头 `编者按：党的二十届四中全会通过的`；`视频`、`财经`、`科技`、`热点`、`国际`、`搜索`、`发布`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **name-link：** 不采用。
- **正文参考：** 使用保存正文中从 `编者按：党的二十届四中全会通过的` 开始，到末尾 `来源：《红旗文稿》` 为止的整篇文章，不抽取局部代替全文。按文章结构划为五个连续区域：①编者按、访谈嘉宾、主持人，以及“人工智能究竟是什么”这一问和张海宁、李艳、陈科良的完整回答；②“人工智能发展的核心要素有哪些”这一问和张海宁、李艳的完整回答；③“推动新一轮人工智能突破的关键因素是什么”这一问和张海宁、聂华、陈科良的完整回答；④“如何认识人工智能的应用场景”这一问和李艳、聂华的完整回答；⑤“如何认识和应对人工智能发展带来的安全风险”这一问和张海宁、聂华、陈科良的完整回答，直至来源行。每一区都包含该区边界内的全部正文。
- **排除：** 评论、作者资料与热门作品、头条热榜、扫码下载和推荐文章。5 区均需 60%，至少 4 区需 80%；只返回导语或第一个问答不能通过。

## 5. risk-061｜Dribbble：web-design 搜索结果

[打开目标网页](https://dribbble.com/search/web-design)

- **网址、语言与目标：** 输入和观察最终网址相同；英文；搜索主题、栏目和首次加载作品网格，不固定作品名单、点赞或浏览量。
- **稳定文字：** `Web design`；`Outstanding web design designs for inspiration`；`Related:`；`Popular`；`Discover`；`Animation`；`Branding`；`Illustration`；`Mobile`；`Print`；`Product Design`；`Typography`；`Filters`。Discover 下这些并列分类属于固定站点导航，既然采用该栏目，就一并检查，不能只挑其中一项。搜索词 Web design 与分类 Web Design 只差大小写，评分归一化后是同一项，因此不重复登记；`Shots`、`Explore`、`Hire Talent`、`Get Hired`、`Community`、`Sign up`、`Log in`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **name-link：** 名称取作品卡片可见标题；链接必须来自同一卡片。允许主机 `dribbble.com`，路径必须完整匹配 `/shots/<数字ID>` 或 `/shots/<数字ID>-<slug>`，可有末尾 `/`。不能把通用的 `Shot Link`、广告标题或作者主页当作品名称。
- **数量与去重：** 独立 HTML 中有 42 个不同 shot ID，N=42，T=34；按数字 shot ID 去重。
- **正文：** 不采用固定作品正文。
- **未选作稳定文字：** `landing page`、`website`、`webdesign`、`user interface`、`web`、`web development` 是本次观察中针对查询词生成的 Related 推荐项，可能随推荐策略变化；现有证据只支持稳定栏目名 `Related:`，不足以把这些成员长期冻结为必需文字。
- **排除：** 作者主页、广告、导航、点赞数、浏览量和 Related 动态推荐成员不计作作品记录。

## 6. risk-101｜哔哩哔哩：综合热门

[打开目标网页](https://www.bilibili.com/v/popular/all/)

- **网址、语言与目标：** 输入和观察最终网址相同；中文；综合热门页面说明、分类和首次加载的 20 个视频卡片，不固定成员、播放量或弹幕数。
- **稳定文字：** `综合热门`、`每周必看`、`入站必刷`、`排行榜`、`全站音乐榜`、`各个领域中新奇好玩的优质内容都在这里~`、`热门规则`；`首页`、`番剧`、`直播`、`游戏中心`、`会员购`、`漫画`、`赛事`、`下载客户端`、`关于我们`、`联系我们`、`用户协议`、`隐私协议`、`帮助中心`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **name-link：** 每条需有可见视频标题及同一卡片的真实视频链接。主机精确为 `www.bilibili.com`，路径完整匹配 `/video/BV<字母数字ID>`，允许末尾 `/`；协议相对 `//www.bilibili.com/...` 是页面真实提供的地址，可按 HTTPS 解析。
- **数量与去重：** N=20，T=16，按 BV 号去重。页脚中的“高级弹幕”示例视频、导航和许可证链接不属于热门列表。
- **正文：** 不采用固定视频标题快照。
- **排除：** 页脚“高级弹幕”示例视频、导航、许可证链接、播放量和弹幕数不计作热门记录。

## 7. risk-102｜腾讯视频：电影频道

[打开目标网页](https://v.qq.com/channel/movie)

- **网址、语言与目标：** 输入和观察最终网址相同；中文；电影分类、筛选和首次加载节目卡片，不进入播放页，不固定片单或评分。
- **稳定文字：** `全部电影`、`院线电影`、`喜剧`、`动作`、`动画电影`、`科幻`、`犯罪`、`爱情`、`独播`、`获奖佳片`、`推荐`、`最新`、`高分`、`免费`；`首页`、`VIP会员`、`电视剧`、`电影`、`综艺`、`动漫`、`少儿`、`NBA`、`下载客户端`、`问题反馈`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **name-link：** 不采用。保存的可见卡片没有提供可核实的普通详情链接，不能根据节目名或内部数据自行拼接播放地址。
- **同卡片字段：** 每条必须同时含有非空节目名称和同一卡片的可见简介；VIP、评分、演员或类型为可选字段，不能代替名称与简介。
- **数量与去重：** 排除游戏广告并合并重复推荐后，独立观察到 N=12 个有效节目，T=10；按节目名称和简介组合去重。
- **正文：** 不冻结当前节目名称、评分和简介为长期答案，只检查字段结构。
- **排除：** 游戏广告、重复推荐、导航和筛选项不计作节目记录；评分、演员或类型不能代替名称与简介。

## 8. risk-diverse-271｜About Sanctum repository service

[打开目标网页](https://dev.sanctum.geek.nz/cgit/?p=about)

- **网址、语言与目标：** 输入和观察最终网址相同；英文；取得 About 页首次加载的完整主体说明，不能只返回站点导航、仓库首页或页脚。
- **稳定文字：** `Tom Ryder—Code`、`Repositories`、`Why not GitHub?`、`Contributing`、`Forking/mirroring`。这些是页面身份和四个长期说明区的标题，独立保存证据和两次当前复核均支持；`index`、`about`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **name-link：** 不采用。正文中的 Tom Ryder、主网站文章、命令手册、邮箱和项目主地址是说明文字内的引用，不是可按数量核对的重复记录列表。
- **正文参考：** 四区覆盖完整主体：① `Repositories` 下个人仓库用途及主网站 Code 章节说明；② `Why not GitHub?` 的完整解释；③ `Contributing` 的完整投稿方式、两个 Git 命令和联系邮箱；④ `Forking/mirroring` 的两个完整段落，包括许可范围、主地址和对专有托管平台的请求。
- **排除：** 顶部 `index`/`about` 导航、搜索表单、图片和图片替代文字，以及会变化的 cgit/git 版本和页脚生成时间。正文 4 区均需 60%，至少 3 区需 80%。

## 9. risk-diverse-027｜Linux Kernel Git

[打开目标网页](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/)

- **网址、语言与目标：** 输入和观察最终网址相同；英文；固定 Linux 主仓库身份与仓库摘要。
- **稳定文字：** `kernel/git/torvalds/linux.git`、`Linux kernel source tree`、`Linus Torvalds`、`about`、`summary`、`refs`、`log`、`tree`、`commit`、`diff`、`stats`、`Clone`、`https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git`。
- **name-link：** 不采用。分支、标签和当前提交属于变化内容，本例不要求固定列表召回。
- **正文：** 不采用固定提交摘要；上述仓库身份文字共同防止只返回 git.kernel.org 首页或其他仓库。
- **排除：** 当前版本号、标签、提交信息、年龄、文件数和生成时间。

## 10. risk-new-102｜DebianInstall

[打开目标网页](https://wiki.debian.org/DebianInstall)

- **网址、语言与目标：** 输入和观察最终网址相同；英文；使用首次加载后直接显示的完整 DebianInstall 文档，不固定当前发行版代号。
- **稳定文字：** `DebianInstall`、`Getting Familiar With Debian Releases`、`Stable`、`Testing`、`Unstable`、`Choosing The Appropriate Installation Media`、`The Network Install Image`、`The Full "CD/DVD" Image`、`Free/Open Source Boot Firmware`、`Non-Free Device Firmware`、`Debian Live images`、`Creating a Bootable Debian USB Flashdrive`、`Booting From a USB Flash Drive`、`Installation`、`Installation FAQ`、`Post-Install Tips`、`See also`。这些是同一文档直接显示的连续章节标题，全部预计随文档主题稳定存在，因此不能只选择目录前半部分；`Wiki`、`FrontPage`、`RecentChanges`、`FindPage`、`HelpContents`、`Login`、`Comments`、`Info`、`Attachments`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **name-link：** 不采用。
- **正文参考：** 使用从 `If you just want to get Debian installed on a regular PC or laptop` 开始，到 `See also` 最后一项 `All Debian images` 结束的完整文档，分五个连续区域：①开头安装建议、Getting Familiar、Stable、Testing、Unstable；②Choosing The Appropriate Installation Media、Network/Full image、两类 firmware、Debian Live images；③Creating a Bootable Debian USB Flashdrive 与 Booting From a USB Flash Drive；④Installation 与完整 Installation FAQ；⑤Post-Install Tips 与完整 See also。每一区都包含边界内全部正文、说明、警告和命令。
- **排除：** 顶部 Wiki 导航、语言入口、目录中重复出现的章节名、最后修改时间、许可证和站点页脚。当前 stable/testing 代号保留在正文参考中，但不单独作为稳定文字。5 区均需 60%，至少 4 区需 80%；只返回发行版介绍不能通过。

## 11. risk-diverse-030｜Wine Bugzilla

[打开目标网页](https://bugs.winehq.org/)

- **网址、语言与目标：** 输入和观察最终网址相同；英文；公开落地页身份、报错步骤和可见任务列表。
- **稳定文字：** `Bug Tracking Database – Main Page`、`Welcome to the WineHQ Bug Tracking System.`、`Before you report a bug`、`Current Bug Task Lists`、`Regressions`、`With download`、`With patch`、`More Help`；`WineHQ`、`Wiki`、`AppDB`、`Bugzilla`、`Forums`、`Task Lists`、`Bug Lists`、`Intro`、`New`、`Browse`、`Search`、`Reports`、`New Account`、`Log In`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **name-link：** 不采用。任务列表是固定入口说明，不是要召回当前 bug 记录。
- **正文参考：** 三区为：`Before you report a bug` 下的创建账户、登录、阅读 Guide、提交 new bug 步骤；`The bugs we are currently working on are organized into Task lists.`；Regressions、With download、With patch 三项及其说明。
- **排除：** 登录输入框值、版本号及页眉页脚重复导航。3 区均需 60%，至少 2 区需 80%。

## 12. risk-diverse-031｜Science Olympiad Student Center Wiki

[打开目标网页](https://scioly.org/wiki/index.php/Main_Page)

- **网址、语言与目标：** 英文；输入会自动从 `/wiki/index.php/Main_Page` 跳到同站 `/wiki/Main_Page`，该跳转允许；目标是 Wiki 主页面。
- **稳定文字：** `Welcome to the Scioly.org Wiki!`、`The collaborative resource that anyone can edit`、`Getting Started`、`Pages That Need Work`、`About Science Olympiad`、`Preparing for Competition`、`Scioly.org Resources`、`Additional Links`、`Invitational Tournaments`、`State Pages`、`National Tournament`；`Forums`、`Wiki`、`Test Exchange`、`Gallery`、`Chat`、`Main Page`、`Recent changes`、`Random page`、`MediaWiki Help`、`Read`、`View source`、`View history`、`Appearance`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 五区覆盖首次加载的全部固定主页内容：①Getting Started 与 Pages That Need Work；②About Science Olympiad；③Preparing for Competition；④Scioly.org Resources 与 Additional Links；⑤Invitational Tournaments、State Pages 和 National Tournament 的固定栏目及资源入口。
- **赛事 name-link：** `2027 Events` 是年份会变化的赛事表，不冻结赛事名单。Division B 与 Division C 分开计数，每条需有同一列中的可见赛事名和页面真实链接；主机 `scioly.org`，路径须是页面实际给出的 Wiki 文章路径，不能用赛事名拼接。两列各 N=23、T=19，按列内规范化链接去重；`2027 National Trial Events`、`Past Events` 和列标题不计赛事。
- **州页 name-link：** State Pages 中州名与同一单元格真实 Wiki 链接对应；N=52、T=42，按规范化链接去重。栏目说明和 National Tournament 区的固定入口纳入正文，不把当前年份的全国赛地点及 Best of 年份冻结为稳定文字。
- **排除：** 文章数量、访问量、最后编辑时间及当前年份/地点的具体赛事值。固定正文 5 区均需 60%，至少 4 区需 80%；两个赛事列和州页分别计数，不能互相补数量。

## 13. risk-diverse-032｜Enlightenment Git

[打开目标网页](https://git.enlightenment.org/enlightenment/enlightenment)

- **网址、语言与目标：** 输入和观察最终网址相同；英文；固定仓库身份及首次加载的完整 README。
- **稳定文字：** 仓库面包屑要求 `enlightenment`，不把页面中分开的斜杠拼入文字；另要求 `README.md`、`Please report bugs/issues at`、`git.enlightenment.org`、`Things that need doing:`、`Requirements`、`Compiling and Installing`、`Quick build help`。同页的 Enlightenment 只差大小写，不重复登记；`Explore`、`Help`、`Register`、`Sign In`、`Code`、`Issues`、`Pull Requests`、`Projects`、`Releases`、`Wiki`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **name-link：** 不采用。提交、分支、标签和计数会变化。
- **正文参考：** 从 `Enlightenment is a Window Manager, Compositor and basic "Desktop Shell"` 到 README 末尾，分五区：①项目能力、资源占用和 X11/Wayland 说明；②About/TODO 与完整 Requirements；③Meson、构建工具、环境变量和库路径；④正常编译、项目选项与 Quick build help 的全部命令；⑤日志、登录管理器、`.xinitrc`/`.xsession`、`startx` 及末尾安全提示。每区均采用边界内全部正文和命令。
- **排除：** 当前提交、作者、时间、计数和文件列表。5 区均需 60%，至少 4 区需 80%。

## 14. risk-diverse-033｜FreeCAD Forum

[打开目标网页](https://forum.freecad.org/)

- **网址、语言与目标：** 输入和观察最终网址相同；英文；论坛身份及首次加载的论坛分类，不固定主题数、帖子数或最新帖子。
- **稳定文字：** `FreeCAD Forum`、`The help and development forum of FreeCAD`、`Help on Using FreeCAD`、`Open Discussions`、`Python Scripting and Macros`；`Search`、`Advanced search`、`Quick links`、`Unanswered topics`、`Active topics`、`Forums`、`FAQ`、`Login`、`Register`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **name-link：** 分类名称必须与同一分类真实链接对应。主机 `forum.freecad.org`，路径精确为 `/viewforum.php`，查询参数 `f` 必须是非空数字分类 ID；会话参数 `sid` 可忽略且不参与身份。
- **数量与去重：** 独立 HTML 中有 52 个不同 `f` 分类入口，N=52，T=42；按 `f` 去重。
- **正文：** 不固定分类下的最新帖子。稳定文字中的三个示例分类还应保留各自可见说明：`Post here for help on using FreeCAD's graphical user interface (GUI).`、`Have some feature requests...`、`Need help, or want to share a macro? Post here!`。
- **排除：** 最新帖子、主题数、帖子数、用户链接、导航和页脚不计作论坛分类。

## 15. risk-diverse-034｜ScummVM Wiki

[打开目标网页](https://wiki.scummvm.org/index.php/Main_Page)

- **网址、语言与目标：** 输入和观察最终网址相同；英文；Wiki 主页面介绍和主要资源区。
- **稳定文字：** `ScummVM :: Wiki`、`Games`、`Users`、`Developers`、`Wiki editors`、`Misc`、`The ScummVM project`。Games 与其余各项同为首页长期资源分区标题，不是当前游戏名单；`Search`、`Create account`、`Log in`、`Home`、`Supported platforms`、`User documentation`、`Read`、`View source`、`View history`、`Privacy policy`、`Disclaimers`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **name-link：** 不采用数量列表。
- **正文参考：** 三区为：从 `Welcome to the ScummVM wiki!` 到 `see our user documentation`；从 `Our project website provides the latest project news` 到 IRC 联系方式；Users、Developers、Wiki Editors、Miscellaneous、The ScummVM project 各区的固定资源名称与说明。
- **排除：** 最后编辑时间、页面历史和导航重复项。3 区均需 60%，至少 2 区需 80%。

## 16. risk-diverse-036｜OpenWrt Wiki

[打开目标网页](https://openwrt.org/start)

- **网址、语言与目标：** 输入和观察最终网址相同；英文；覆盖首页首次加载的完整中央内容，不把当前版本或价格冻结为稳定文字。
- **稳定文字：** `Welcome to the OpenWrt Project`、`Learn about OpenWrt`、`Why use OpenWrt?`、`Extensibility`、`Security`、`Performance and Stability`、`Strong Community Support`、`Research`、`Open Source/No additional cost`、`Contributing`、`OpenWrt wants you!`。`OpenWrt Joins Software Freedom Conservancy (SFC)` 属于公告标题，虽然本次证据存在，但不再冻结为必需稳定文字；`OpenWrt Wiki`、`Tools`、`Log In`、`Recent Changes`、`Media Manager`、`Sitemap`、`Show pagesource`、`Old revisions`、`Backlinks`、`Back to top`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **正文参考：** 五区为：①完整项目介绍及支持设备/项目说明；②OpenWrt One 公告的完整直接显示内容；③当前稳定系列、当前稳定版本、旧稳定系列和旧稳定版本的完整说明及真实入口；④Why use OpenWrt 的全部七项说明；⑤OpenWrt Joins Software Freedom Conservancy 与 OpenWrt wants you 的完整说明。版本号、日期和价格保留在当次正文参考中，但不单独设为稳定文字。
- **排除：** 左侧站点导航、翻译列表、登录工具、最后修改时间和页脚。5 区均需 60%，至少 4 区需 80%；只返回项目介绍或 Why use OpenWrt 不能通过。

## 17. risk-diverse-038｜ArchWiki

[打开目标网页](https://wiki.archlinux.org/title/Main_page)

- **网址、语言与目标：** 输入和观察最终网址相同；英文；ArchWiki 主页面介绍和主要导航内容。
- **稳定文字：** 顶部站点导航 `Home`、`Packages`、`Forums`、`Wiki`、`GitLab`、`Security`、`AUR`、`Download`；页面与侧栏 `ArchWiki`、`Main page`、`Table of contents`、`Getting involved`、`Help`、`Contributing`、`Search`、`Appearance`；页面操作 `Read`、`View source`、`View history`；主体分别要求 `Welcome to the`、`your source for Arch Linux documentation on the web.`、`The distribution`、`Our community`、`Wiki interaction`。欢迎句中的 ArchWiki 是单独显示的链接文字，已由前面的 ArchWiki 组检查，不再把三段拼成一个稳定文字。所有文字都在独立保存的首次页面文本中，分布于顶部、侧栏、页面操作和完整主体，全部必需。
- **name-link：** 不采用数量列表。
- **正文参考：** 三区为：欢迎语和 `Visit the Table of contents for a listing of article categories.`；The distribution 下 Overview、FAQ、Arch compared、Installation guide、General recommendations、List of applications 及说明；Our community 与 Wiki interaction 下各固定入口及说明。
- **排除：** 语言数量、旧版本号、最后编辑时间、当前登录状态和外观选项的当前选中值。`Color (beta)` 及 Small/Standard/Large 等皮肤选项不冻结，因为名称和可用项可能随 MediaWiki 皮肤版本变化。3 区均需 60%，至少 2 区需 80%；只返回顶部导航、侧栏或主体其中一部分均不能通过；响应式导航中的 `Recent changes` 不参与通过判定。

## 18. risk-diverse-039｜FreeBSD SVNweb

[打开目标网页](https://svnweb.freebsd.org/base/)

- **网址、语言与目标：** 输入和观察最终网址相同；英文；`base` 根目录的可见目录/文件列表。仓库已经迁移，列表内容历史稳定，但仍按结构和数量检查。
- **稳定文字：** `Index of /`、`File`、`Rev.`、`Age`、`Author`、`Last log entry`、`Mark the repository as being converted to Git.`；`ViewVC Help`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定。
- **name-link：** 主机 `svnweb.freebsd.org`；条目路径需完整匹配 `/base/<单层目录>/` 或 `/base/<单层文件名>`。排序表头和 `?view=log` 的版本链接不是独立文件条目。
- **数量与去重：** 10 个目录加 `ROADMAP.txt`，N=11，T=9；按去掉查询参数后的条目路径去重。
- **正文：** 不采用 Properties 表或每条最新日志作为固定正文。
- **排除：** 排序表头、`?view=log` 版本链接、Properties 表和每条最新日志不计作目录或文件记录。

## 19. risk-diverse-040｜Purism GitLab：Phosh

[打开目标网页](https://source.puri.sm/Librem5/phosh)

- **网址、语言与目标：** 输入和观察最终网址相同；英文；已归档 Phosh 项目身份与迁移说明。
- **稳定文字：** 项目标识分别使用页面实际显示的 `Librem5` 与 `phosh`，并要求 `This project is archived. Its data is read-only.`、`Moved to GNOME/World/Phosh . For PureOS or packaging see here.`、`README.md`。保存正文把面包屑拆成两行，没有证据支持把斜杠当作必须出现的可见文字；大小写不同的 Phosh 不重复登记成第二组；`Homepage`、`Explore`、`Register`、`Sign in`、`Project`、`Members`、`Work items`、`Branches`、`Merge requests`、`Pipelines`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **name-link：** 不采用仓库变化列表。
- **正文参考：** 两区为 `As of 2021-08-02 Upstream development moved to GNOME's gitlab. The new location for code and issues is at https://gitlab.gnome.org/World/Phosh/phosh.`；`The packaging for the Librem 5 and PureOS lives at https://source.puri.sm/Librem5/debs/pkg-phosh.`
- **排除：** 提交数、标签数、发布数和最近提交。两区均需 60%，其中至少 1 区需 80%。

## 20. risk-diverse-041｜Haiku Trac

[打开目标网页](https://dev.haiku-os.org/)

- **网址、语言与目标：** 输入和观察最终网址相同；英文；开发跟踪器身份和公开项目导航说明。
- **稳定文字：** `Welcome to the Haiku development tracker`、`Logging in`、`Using the tracker`、`Browsing the source`、`Useful information`；`Search`、`Login`、`Help/Guide`、`About Trac`、`Preferences`、`Register`、`Wiki`、`Timeline`、`Roadmap`、`Browse Source`、`View Tickets`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **name-link：** 不采用当前 ticket 或源码列表。
- **正文参考：** 三区为：Logging in 的账户、邮件地址和验证邮件说明；Using the tracker 下 BugTrackerEtiquette、ReportingBugs、SubmittingPatches、TicketLife 的说明；Browsing the source 与 Useful information 下 git、cgit、EasyTasks、FutureHaikuFeatures 等说明。
- **排除：** 最后修改时间和底部 Trac 版本。3 区均需 60%，至少 2 区需 80%。

## 21. risk-diverse-042｜Devuan Git：installer-iso

[打开目标网页](https://git.devuan.org/devuan/installer-iso)

- **网址、语言与目标：** 输入和观察最终网址相同；英文；固定项目身份和首次加载的完整 README。
- **稳定文字：** 仓库面包屑分别要求 `devuan`、`installer-iso`，不把页面中分开的斜杠拼入文字；另要求 `README.md`、`Devuan Installer ISO`、`How to Build an ISO`、`Using APT Proxy`、`Including local udebs`、`Including local debs`、`Help?`、`Code`、`Clone`。The pool Directory 是由普通文字、链接和普通文字拼出的标题，不再作为单个稳定文字；该分区仍由正文规则覆盖；`Explore`、`Help`、`Register`、`Sign In`、`Issues`、`Pull Requests`、`Projects`、`Releases`、`Wiki`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **name-link：** 不采用文件或提交列表。
- **正文参考：** 从 `This project is tailored for building the Devuan installation ISOs` 到 Help 末尾，分五区：①项目用途及 ISO 类型列表；②The pool Directory 的完整说明；③How to Build an ISO 的环境、配置和构建命令；④Using APT Proxy 与 Including local udebs；⑤Including local debs 与 Help 的完整说明。每区均包含边界内所有正文和命令。
- **排除：** 分支、标签、提交、日期和文件列表。5 区均需 60%，至少 4 区需 80%。

## 22. risk-diverse-045｜CFA Archive

[打开目标网页](https://www.cfaarchive.org/)

- **网址、语言与目标：** 输入和观察最终网址相同；英文；站点身份和自动显示的收藏站说明。
- **稳定文字：** `Comic Fanfiction Authors Archive`、`The CFAA is a fan-created, fan-run, noncommercial archive for fanworks based around comic books or comic-adjacent animation.`、`With a CFAA account, you can:`、`Find your favorites`；`Browse`、`Works`、`Bookmarks`、`Tags`、`Collections`、`Search`、`People`、`Community`、`Discord`、`About`、`About Us`、`News`、`FAQ`、`Wrangling Guidelines`、`Donate or Volunteer`、`Links`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **新闻 name-link：** 不冻结当前新闻标题。每条需有同一卡片的可见标题和页面真实链接；主机 `www.cfaarchive.org`，路径完整匹配 `/admin_posts/<数字ID>`，查询参数及片段不参与身份。N=3，T=3，按数字 ID 去重；发布日期、评论数和摘要属于同一卡片必要字段，具体值可变化。
- **正文参考：** 四区覆盖首次加载的全部固定主页内容：①完整档案说明及 Arkham Asylum 说明；②邀请方式说明、账户功能列表和 tagging rules 提醒；③Find your favorites 的完整 fandom 分类；④News 栏目的固定结构说明。变化新闻本身由 name-link 和字段规则覆盖。
- **排除：** fandom/user/work 数量不作为稳定文字；当前新闻标题、日期、评论数不冻结为固定答案。固定正文 4 区均需 60%，至少 3 区需 80%。

## 23. risk-diverse-047｜Proxmox Bugzilla

[打开目标网页](https://bugzilla.proxmox.com/)

- **网址、语言与目标：** 输入和观察最终网址相同；英文；公开 Bugzilla 首页身份及搜索/帮助入口。
- **稳定文字：** `Proxmox Bugzilla – Main Page`、`Welcome to Proxmox Bugzilla`、`File a Bug`、`Search`、`Open a New Account`、`Documentation`、`Common Queries:`、`Product Documentation:`、`Get Help:`；`Home`、`New`、`Browse`、`Reports`、`New Account`、`Log In`、`Privacy`、`Legal`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **name-link：** 不采用 bug 记录列表；页面只展示固定操作入口和文档入口。
- **正文：** 不另设长正文覆盖。上述稳定组共同要求首页核心界面完整出现；单独返回站名或登录框不能通过。
- **排除：** Bugzilla 版本号、页眉页脚重复导航。

## 24. risk-diverse-048｜ReactOS Wiki

[打开目标网页](https://reactos.org/wiki/Main_Page)

- **网址、语言与目标：** 输入和观察最终网址相同；英文；ReactOS 开发 Wiki 主页面内容。
- **稳定文字：** `Welcome to the ReactOS Development Wiki`、`DEVELOPING`、`TESTING`、`BUILDING`、`DEBUGGING`、`TRANSLATING`、`DOCUMENTATION`、`STATUS`、`COLLABORATING`、`The layman's guides.`、`Articles by Petr Akhlamov`；`Read`、`View source`、`View history`、`Main page`、`Recent changes`、`Search`、`Back to Website`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **name-link：** 不采用数量列表。
- **正文参考：** 四区覆盖完整主页内容：①DEVELOPING 与 TESTING；②BUILDING、DEBUGGING、TRANSLATING；③DOCUMENTATION、STATUS、COLLABORATING；④The layman's guides 与 Articles by Petr Akhlamov 的全部说明和资源入口。
- **排除：** 最后编辑时间、旧版本 URL 和导航菜单。4 区均需 60%，至少 3 区需 80%。

## 25. risk-diverse-049｜Arch Linux 32 Packages

[打开目标网页](https://www.archlinux32.org/packages/)

- **网址、语言与目标：** 输入和观察最终网址相同；英文；包搜索页身份和第一页可见包记录，不固定当前包名、版本或日期。
- **稳定文字：** `Arch Linux Official Package Search`、`Enter search criteria`、`Arch`、`Repository`、`Keywords`；表头分别要求 `Repo`、`Name`、`Version`、`Bugs`、`Build Date`、`Last Updated`、`Delete`。Arch 已在前面单独要求，不重复登记；顶部导航和表头中的 Bugs 评分归一化后也是同一项，只登记一次；页面本身没有用斜杠连接这些表头；`Home`、`Packages`、`Forums`、`Mailing List`、`Download`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **name-link：** 包名和同一表格行的真实链接必须对应。主机 `www.archlinux32.org`，路径完整匹配 `/packages/(any|i486|i686|pentium4)/<仓库>/<包名>/`；不能根据包名自行拼接。
- **数量与去重：** 首页独立观察到 N=100 条包记录，T=80；按完整详情路径去重，因此同名包在不同架构/仓库仍是不同记录。
- **同条目字段：** 每条还需来自同一行的 Arch、Repo、Name、Version、Build Date、Last Updated；Bugs/Delete 仅在页面实际提供时可选。当前公共 `named_link` 契约不能同时强制这些额外字段，后续落配置时需补足，不能只保留链接数量。
- **正文：** 不采用固定包名、版本或日期正文；变化记录由 name-link、同条目字段、去重和数量覆盖。
- **排除：** 总匹配包数、总页数和当天版本值作为固定答案。

## 26. risk-diverse-050｜Slackware Git

[打开目标网页](https://git.slackware.nl/current/)

- **网址、语言与目标：** 输入和观察最终网址相同；英文；`current` 仓库身份与稳定摘要。
- **稳定文字：** 面包屑分别要求 `index`、`current`，不把页面中单独显示的冒号拼入文字；另要求 `Tracking development of slackware-current`、`alien@slackware.com`、`summary`、`refs`、`log`、`tree`、`commit`、`diff`、`stats`；`Clone`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **name-link：** 不采用。分支、标签、下载包和提交会变化。
- **正文：** 不冻结当前提交日志；稳定身份文字共同防止返回其他 Slackware 仓库。
- **排除：** 日期标签、提交消息、作者、年龄、文件数和行数。

## 27. risk-diverse-051｜Duke Digital Repository

[打开目标网页](https://repository.duke.edu/)

- **网址、语言与目标：** 输入和观察最终网址相同；英文；Duke 数字仓储身份与四类自动显示的收藏/学术资源说明。浏览器保存正文为目标证据；同轮普通 HTTP 是 Anubis 拒绝页，不能当正文。
- **稳定文字：** `Digital Repositories at Duke`、`Collections and scholarship from Duke University Libraries`、`LIBRARY COLLECTIONS`、`DUKE SCHOLARSHIP`；`Duke University Libraries`、`REPOSITORY`、`COLLECTIONS & ARCHIVES`、`Search`、`Login`、`Ask`、`Menu`、`About the Repositories`、`Contact Us`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **name-link：** 不采用变化作品列表。
- **正文参考：** 三区为：Digitized Collections 及其说明；Acquired Materials 及其说明；Research Data Repository 与 DukeSpace 各自说明。
- **排除：** Anubis `Making sure you're not a bot!` 页面、搜索框重复项和页脚地址。3 区均需 60%，至少 2 区需 80%。

## 28. risk-diverse-055｜hebis Discovery System

[打开目标网页](https://tufind.hds.hebis.de/)

- **网址、语言与目标：** 输入和观察最终网址相同；英文；ULB Darmstadt 搜索门户身份与两类搜索资源说明。页面同时提供 DE/EN 切换，但本例固定英文内容。
- **稳定文字：** `The search portal of ULB Darmstadt`、`Books & More`、`Articles & More`、`Advanced`、`Search History`；`ULB Darmstadt`、`Your Account`、`English`、`Deutsch`、`Find`、`Services`、`Help`、`Legal`、`Privacy Policy`、`Imprint`、`Contacts`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **name-link：** 不采用搜索结果列表。
- **正文参考：** 两区为 Books & more 的完整说明；Articles & More 从 `Millions of documents` 到 `for comprehensive special research: Datenbankinfosystem` 的完整说明。
- **排除：** JavaScript disabled 警告、登录状态、Loading 占位和页脚。两区均需 60%，其中至少 1 区需 80%。

## 29. risk-diverse-057｜Xe Iaso

[打开目标网页](https://xeiaso.net/)

- **网址、语言与目标：** 输入和观察最终网址相同；英文；作者身份、简介和首次加载的 Recent Articles。
- **稳定文字：** `Xe Iaso`、`CEO @ Techaro - Ottawa, CAN`、`Recent Articles`、`Support My Work`、`Notable Publications`、`Highlighted Projects`、`Quick Links`；`Blog`、`Contact`、`Donate`、`Talks`、`Signalboost`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **name-link：** Recent Articles 的可见标题必须与同条真实链接对应。允许主机 `xeiaso.net`、`www.tigrisdata.com`、`anubis.techaro.lol`；路径需匹配 `/blog/<可选年份>/<slug>/` 或 `/notes/<年份>/<slug>/`。外站链接是页面实际提供的文章链接，不能一律强制回本站。
- **数量与去重：** N=8，T=7；按主机加完整路径去重，日期不是名称。
- **正文参考：** 四区覆盖首页首次加载的全部固定主体内容：①完整作者简介；②Support My Work 的说明与入口；③Notable Publications 的六项标题及各自完整说明；④Highlighted Projects 的六项名称及说明，以及 Quick Links。Recent Articles 由上面的 name-link 和数量规则覆盖。
- **排除：** 当前文章标题不固定为长期名单；版权年份、文章日期、站点版本和页脚标签不作为稳定值。固定正文 4 区均需 60%，至少 3 区需 80%。

## 30. risk-diverse-059｜Superlove

[打开目标网页](https://superlove.sayitditto.net/)

- **网址、语言与目标：** 输入和观察最终网址相同；英文；站点身份和自动显示的主页面说明。
- **稳定文字：** `superlove`、`Superlove implies the existence of underwhelming hatred`、`for fanfiction, original fiction, multimedia fanworks, and more`、`superlove is a project by`、`girl on the moon`、`superlove is:`、`Find your favorites`。项目署名在保存正文中分成两段，不再合成一句稳定文字；`Browse`、`Works`、`Bookmarks`、`Tags`、`Collections`、`Search`、`People`、`About`、`About Us`、`News`、`FAQ`、`Wrangling Guidelines`、`Donate or Volunteer`、`Site Status`、`Links`。以上稳定文字均来自绑定的首次加载证据，全部参与任务通过判定；排除项中的“导航”仅指未列入这里的其他通用、状态性或易变导航。
- **新闻 name-link：** 不冻结当前新闻标题。每条需有同一卡片的可见标题和页面真实链接；主机 `superlove.sayitditto.net`，路径完整匹配 `/admin_posts/<数字ID>`，查询参数及片段不参与身份。N=3，T=3，按数字 ID 去重；发布日期、评论数和摘要属于同一卡片必要字段，具体值可变化。
- **正文参考：** 五区覆盖首次加载的完整主页主体：①站点用途句；②项目身份、两个 DAYS SINCE 状态和完整邀请说明；③`superlove is:` 的全部自我说明；④Find your favorites 的说明和完整 fandom 分类；⑤News 栏目的固定结构说明。变化新闻本身由 name-link 和字段规则覆盖。
- **排除：** fandom/user/work 数量、两个 DAYS SINCE 数值、邀请状态和当前新闻值不设为稳定文字；构建版本、页脚和图片文字排除。固定正文 5 区均需 60%，至少 4 区需 80%。

---

## 本轮发现、尚未执行的实现事项

以下是已经确认的规则表达问题，不是产品测试结果：

1. **Arch Linux 32 包列表需要 name-link 与同一行附加字段同时检查。** 当前公共 `named_link` 只内建名称和 URL，不能完整强制 Arch、Repo、Version、Build Date、Last Updated。
2. **腾讯视频需要无普通详情链接的同卡片名称＋简介结构。** 可用 `name_description` 表达，但正式落配置前要用保存正文验证卡片边界解析，不能跨卡片拼接。

这两点在后续规则落地阶段处理。当前没有为了适配现有实现而降低规则，也没有调用任何被测产品。
