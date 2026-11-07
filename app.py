# ---------------------------------------------------------------------------
# 已冻结（2026-11-06）：这是早期的浅色 Streamlit 试验版，已被 HUD 单文件原型取代。
# 当前原型：preview.html（v0.3，双助手 / 记忆 / 收集 / 设置）。
# 后续正式实现：React + Vite + TS（前端）+ FastAPI + SQLite（后端），见 docs/PLAN.md。
# 保留本文件仅为历史对照，不要在此继续开发，也不要据此判断当前界面方向。
# ---------------------------------------------------------------------------
import streamlit as st
from datetime import date, timedelta

st.set_page_config(page_title="Jarvis · 个人工作台", page_icon="✦", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Noto+Sans+SC:wght@400;500;600;700&display=swap');
:root { --ink:#18251f; --muted:#78847c; --line:#e9ece7; --paper:#f7f8f5; --green:#2e664d; --lime:#d9f36a; }
html, body, [class*="css"] { font-family:'DM Sans','Noto Sans SC',sans-serif; }
.stApp { background:var(--paper); color:var(--ink); }
[data-testid="stSidebar"] { background:#f0f3ee; border-right:1px solid #e4e9e2; }
[data-testid="stSidebar"] > div { padding-top:1.25rem; }
.brand { font-size:22px; letter-spacing:-.8px; font-weight:700; margin:4px 0 3px; }
.brand span { color:var(--green); }
.subbrand { font-size:12px; color:var(--muted); margin-bottom:28px; }
.eyebrow { color:var(--green); font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:1.4px; }
.hero { background:#e8efe8; border:1px solid #dce6dc; border-radius:20px; padding:26px 30px; margin:8px 0 22px; position:relative; overflow:hidden; }
.hero h1 { margin:7px 0 5px; font-size:30px; letter-spacing:-1.3px; color:#1d3026; }
.hero p { margin:0; color:#66746a; font-size:14px; }
.hero-mark { position:absolute; right:24px; top:10px; color:#c1d2c1; font-size:105px; line-height:1; }
.metric { background:#fff; border:1px solid var(--line); border-radius:15px; padding:16px 18px; min-height:91px; }
.metric-label { color:var(--muted); font-size:12px; }
.metric-value { color:var(--ink); font-weight:700; font-size:27px; letter-spacing:-1px; margin-top:5px; }
.metric-note { color:#568064; font-size:11px; margin-top:1px; }
.section-head { display:flex; align-items:center; justify-content:space-between; margin:25px 0 12px; }
.section-head h3 { margin:0; font-size:17px; letter-spacing:-.4px; }
.section-hint { color:var(--muted); font-size:12px; }
.panel { background:#fff; border:1px solid var(--line); border-radius:16px; padding:17px 19px; margin-bottom:10px; }
.mail-row { display:flex; gap:13px; padding:12px 0; border-bottom:1px solid #f0f1ee; }
.mail-row:last-child { border:0; padding-bottom:2px; }
.avatar { width:34px; height:34px; display:flex; justify-content:center; align-items:center; border-radius:11px; background:#edf2ec; color:#416c51; font-size:12px; font-weight:700; flex-shrink:0; }
.mail-title { font-weight:600; font-size:13px; color:#29372f; }
.mail-from { color:#78847c; font-size:11px; margin-top:2px; }
.mail-summary { color:#67736b; font-size:12px; margin-top:5px; line-height:1.5; }
.pill { display:inline-block; background:#f3f5f1; border-radius:100px; padding:4px 9px; color:#627068; font-size:10px; margin:6px 4px 0 0; }
.pill-hot { background:#fff0e8; color:#a65f38; }
.todo-line { display:flex; align-items:flex-start; gap:10px; padding:10px 0; border-bottom:1px solid #f0f1ee; }
.todo-line:last-child { border:0; }
.todo-text { font-size:13px; font-weight:500; }
.todo-due { color:#889189; font-size:11px; margin-top:3px; }
.post-card { background:#fff; border:1px solid var(--line); border-radius:14px; padding:15px 16px; min-height:144px; }
.post-author { font-size:11px; color:#78847c; margin-bottom:8px; }
.post-copy { font-size:13px; line-height:1.55; color:#344139; }
.post-footer { color:#89938c; font-size:10px; margin-top:10px; }
.approval { background:#fff; border:1px solid #eadfca; border-left:3px solid #d2a35b; border-radius:13px; padding:13px 15px; margin-bottom:9px; }
.approval-label { color:#98723c; font-size:10px; font-weight:700; letter-spacing:.7px; }
.approval-title { color:#29372f; font-weight:600; font-size:13px; margin:5px 0 2px; }
.approval-meta { color:#78847c; font-size:11px; }
.footer-note { color:#9aa39b; font-size:10px; margin-top:14px; }
div[data-testid="stMetric"] { background:white; border:1px solid var(--line); border-radius:15px; padding:13px 17px; }
div[data-testid="stMetricLabel"] { color:#78847c; }
button[kind="primary"] { background:var(--green); border:0; }
</style>
""", unsafe_allow_html=True)

if "todos_done" not in st.session_state:
    st.session_state.todos_done = set()
if "approved" not in st.session_state:
    st.session_state.approved = set()

with st.sidebar:
    st.markdown('<div class="brand">✦ <span>Jarvis</span></div><div class="subbrand">你的个人 AI 工作台</div>', unsafe_allow_html=True)
    pages = ["总览", "邮件", "X 收藏与点赞", "知识库", "待办", "待审批"]
    page = st.radio("工作空间", pages, label_visibility="collapsed")
    st.markdown("<div style='height:18px'></div><div class='eyebrow'>演示环境</div><div style='font-size:12px;color:#718077;margin-top:6px'>使用模拟数据 · 未连接任何账户</div>", unsafe_allow_html=True)
    st.markdown("<div style='height:22px'></div>", unsafe_allow_html=True)
    st.button("⟳  同步数据", use_container_width=True, disabled=True, help="原型阶段不连接 Gmail 或 X")
    st.markdown("<div class='footer-note'>隐私优先 · 外部操作需要你确认</div>", unsafe_allow_html=True)

emails = [
    ("M", "Maya Chen", "关于周五产品评审的材料", "请在周四下班前补上用户访谈的关键发现，评审会需要用到。", "需要回复", "高优先", True),
    ("N", "Notion", "你的每周工作摘要", "本周有 3 个页面更新，包含 Jarvis 竞品调研和用户访谈记录。", "摘要", "工作", False),
    ("L", "Leo Wang", "Re: 会议时间确认", "周三下午 3 点可以，我会提前把议程发给你。", "待确认", "日程", False),
]
posts = [
    ("@designsystems", "好的产品界面会让复杂的事情变得简单。先把用户最常做的三件事做顺，再慢慢扩展。", "产品设计 · 2 小时前", "产品"),
    ("@linh_builds", "用小步迭代做 AI 工具：每次只让模型给建议，把最终决定留给用户。", "AI 工具 · 昨天", "AI 开发"),
]
todos = [
    ("整理用户访谈的 5 条关键发现", "今天 · 来自 Maya 的邮件", "高优先"),
    ("完成 Jarvis 首页低保真草图", "明天 · 产品规划", "进行中"),
    ("阅读 OAuth 权限申请说明", "11 月 10 日 · 技术调研", "普通"),
    ("把喜欢的产品案例归入知识库", "11 月 12 日 · X 收藏", "普通"),
]
notes = [
    ("Jarvis MVP：先建议，后执行", "产品设计 · 11 月 5 日", "首版应先完成分类、摘要、搜索与待办关联。发送、删除和发布操作要走逐项审批。"),
    ("邮件分级规则草案", "工作流程 · 11 月 4 日", "按截止时间、是否需要回复、是否来自重要联系人进行初步分级；低置信度项目交给用户判断。"),
    ("AI 助手界面参考", "产品灵感 · 11 月 2 日", "减少同时出现的信息量。首页只呈现今天要处理的事，并提供返回来源的入口。"),
]

def section(title, hint=""):
    st.markdown(f'<div class="section-head"><h3>{title}</h3><span class="section-hint">{hint}</span></div>', unsafe_allow_html=True)

def render_mail_cards(limit=3):
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    for initial, sender, subject, summary, tag, priority, hot in emails[:limit]:
        tag_html = f'<span class="pill">{tag}</span>'
        hot_html = f'<span class="pill pill-hot">{priority}</span>' if hot else ""
        st.markdown(f'''<div class="mail-row"><div class="avatar">{initial}</div><div style="flex:1"><div class="mail-title">{subject}</div><div class="mail-from">{sender} · {tag_html}{hot_html}</div><div class="mail-summary">{summary}</div></div></div>''', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

def render_todos(limit=4):
    for i, (title, due, importance) in enumerate(todos[:limit]):
        cols = st.columns([0.12, 0.88])
        with cols[0]:
            checked = st.checkbox("完成", key=f"todo_{i}", value=i in st.session_state.todos_done, label_visibility="collapsed")
        if checked:
            st.session_state.todos_done.add(i)
        else:
            st.session_state.todos_done.discard(i)
        style = "text-decoration:line-through;color:#a0a8a1" if checked else ""
        with cols[1]:
            st.markdown(f'<div class="todo-text" style="{style}">{title}</div><div class="todo-due">{due} · {importance}</div>', unsafe_allow_html=True)

if page == "总览":
    st.markdown('<div class="hero"><div class="eyebrow">星期五 · 你的工作台</div><h1>早上好，今天从容一点。</h1><p>Jarvis 帮你把重要的事放在眼前，决定权始终在你手上。</p><div class="hero-mark">✦</div></div>', unsafe_allow_html=True)
    m1, m2, m3, m4 = st.columns(4)
    for col, label, value, note in [(m1,"待处理邮件","08","2 封建议优先处理"),(m2,"今日待办",str(4-len(st.session_state.todos_done)),"1 项来自邮件"),(m3,"待你审批","02","邮件草稿与整理建议"),(m4,"新收集内容","12","来自收藏与知识库")]:
        with col:
            st.markdown(f'<div class="metric"><div class="metric-label">{label}</div><div class="metric-value">{value}</div><div class="metric-note">{note}</div></div>', unsafe_allow_html=True)
    left, right = st.columns([1.12, .88], gap="large")
    with left:
        section("优先邮件", "AI 建议 · 由你判断")
        render_mail_cards(3)
    with right:
        section("今天要做", "{0} 项未完成".format(4-len(st.session_state.todos_done)))
        with st.container(border=True):
            render_todos(4)
        section("需要你确认", "所有外部操作都在这里")
        st.markdown('<div class="approval"><div class="approval-label">邮件草稿 · 等待确认</div><div class="approval-title">回复 Maya：周五产品评审材料</div><div class="approval-meta">收件人、主题和正文将在发送前完整展示</div></div><div class="approval"><div class="approval-label">整理建议 · 等待确认</div><div class="approval-title">为 6 条收藏添加「产品灵感」标签</div><div class="approval-meta">仅影响 Jarvis 内部分类，不会修改 X</div></div>', unsafe_allow_html=True)
    section("最近收藏", "整理只发生在 Jarvis 内")
    pcols = st.columns(2)
    for col, (author, copy, meta, tag) in zip(pcols, posts):
        with col:
            st.markdown(f'<div class="post-card"><div class="post-author">{author}　·　{meta}</div><div class="post-copy">{copy}</div><div class="post-footer">♡　已点赞　　<span class="pill">{tag}</span></div></div>', unsafe_allow_html=True)
    st.markdown('<div class="footer-note">演示数据仅用于体验界面。连接服务、读取数据或执行操作目前均未启用。</div>', unsafe_allow_html=True)

elif page == "邮件":
    st.markdown('<div class="eyebrow">邮件工作区</div><h1 style="margin:5px 0 0;font-size:28px">把收件箱理出头绪。</h1><p style="color:#78847c;margin-top:5px">摘要和优先级仅供参考；回复发送前必须由你确认。</p>', unsafe_allow_html=True)
    q = st.text_input("搜索邮件", placeholder="搜索发件人、主题或内容…")
    filt = st.selectbox("视图", ["全部邮件", "需要回复", "高优先", "已归档"])
    matching = [e for e in emails if not q or q.lower() in " ".join(e).lower()]
    if filt == "需要回复": matching = [e for e in matching if e[4] == "需要回复"]
    if filt == "高优先": matching = [e for e in matching if e[6]]
    for initial, sender, subject, summary, tag, priority, hot in matching:
        with st.container(border=True):
            st.markdown(f"**{subject}**　　`{priority if hot else tag}`\n\n{sender}　·　{summary}")
            a, b, c = st.columns([1,1,4])
            with a: st.button("生成回复草稿", key=f"draft_{initial}")
            with b: st.button("转为待办", key=f"task_{initial}")
    with st.expander("回复草稿预览（模拟）"):
        st.text_input("收件人", value="maya@example.com")
        st.text_input("主题", value="Re: 关于周五产品评审的材料")
        st.text_area("正文", value="Hi Maya，收到。我会在周四下班前补充用户访谈的关键发现，并提前发给你。", height=110)
        st.warning("这是本地演示草稿。发送功能未连接；真实邮件发送需要逐封审批。")

elif page == "X 收藏与点赞":
    st.markdown('<div class="eyebrow">个人内容收藏</div><h1 style="margin:5px 0 0;font-size:28px">把好内容留在手边。</h1><p style="color:#78847c;margin-top:5px">下面是模拟内容。标签只保存在 Jarvis，不会回写 X。</p>', unsafe_allow_html=True)
    xq = st.text_input("搜索收藏", placeholder="搜索内容、作者或标签…")
    c1, c2 = st.columns(2)
    with c1: st.selectbox("来源", ["全部", "书签", "点赞"])
    with c2: st.selectbox("主题", ["全部主题", "产品", "AI 开发"])
    grid = st.columns(2)
    for i, (author, copy, meta, tag) in enumerate(posts):
        if xq and xq.lower() not in (author+copy+meta+tag).lower(): continue
        with grid[i % 2]:
            st.markdown(f'<div class="post-card"><div class="post-author">{author}　·　{meta}</div><div class="post-copy">{copy}</div><div class="post-footer">♡　已点赞　　<span class="pill">{tag}</span></div></div>', unsafe_allow_html=True)
            st.text_input("添加 Jarvis 标签", key=f"xtag_{i}", placeholder="例如：待读、灵感…")
    st.info("官方 X API 的连接、同步和费用统计尚未启用。")

elif page == "知识库":
    st.markdown('<div class="eyebrow">个人知识库</div><h1 style="margin:5px 0 0;font-size:28px">收集、连接、再利用。</h1><p style="color:#78847c;margin-top:5px">先从手动笔记和本地文件开始；当前内容均为模拟数据。</p>', unsafe_allow_html=True)
    kq = st.text_input("搜索知识", placeholder="搜索笔记内容或主题…")
    with st.expander("＋ 新建笔记", expanded=False):
        st.text_input("标题")
        st.text_area("内容", height=100)
        st.button("保存到知识库")
    for title, meta, body in notes:
        if kq and kq.lower() not in (title+meta+body).lower(): continue
        with st.container(border=True):
            st.markdown(f"**{title}**　`{meta}`\n\n{body}")
            st.button("提取待办", key=f"note_{title}")

elif page == "待办":
    st.markdown('<div class="eyebrow">任务管理</div><h1 style="margin:5px 0 0;font-size:28px">一步一步推进。</h1><p style="color:#78847c;margin-top:5px">每条任务都可以回到它的来源。</p>', unsafe_allow_html=True)
    with st.form("new_todo", clear_on_submit=True):
        new_title = st.text_input("新增待办", placeholder="接下来要做什么？")
        due = st.date_input("截止日期", value=date.today()+timedelta(days=1))
        add = st.form_submit_button("添加待办")
        if add and new_title:
            st.success(f"已加入演示待办：{new_title}（{due:%m月%d日}）")
    section("全部待办", "按紧急程度排列")
    with st.container(border=True): render_todos(4)

elif page == "待审批":
    st.markdown('<div class="eyebrow">安全控制</div><h1 style="margin:5px 0 0;font-size:28px">每个动作，都由你拍板。</h1><p style="color:#78847c;margin-top:5px">此原型只演示审批交互，不会连接服务或执行任何外部操作。</p>', unsafe_allow_html=True)
    section("等待确认", "请逐项查看操作内容")
    st.markdown('<div class="approval"><div class="approval-label">邮件草稿 · 模拟</div><div class="approval-title">回复 Maya Chen：关于周五产品评审的材料</div><div class="approval-meta">收件人：maya@example.com · 主题和正文需在发送前确认</div></div>', unsafe_allow_html=True)
    with st.expander("查看完整邮件草稿"):
        st.write("收件人：maya@example.com")
        st.write("主题：Re: 关于周五产品评审的材料")
        st.text_area("正文", "Hi Maya，收到。我会在周四下班前补充用户访谈的关键发现，并提前发给你。", height=100, key="approval_body")
        st.error("模拟演示：即使点击确认，也不会发送邮件。真实版本会再次核对收件人与正文。")
    approved = st.checkbox("我已检查完整内容，理解真实操作会产生外部影响", key="confirm_send")
    st.button("确认并继续", type="primary", disabled=not approved, help="当前为模拟按钮，不会执行外部操作")
    section("整理建议", "只改变 Jarvis 内部数据")
    st.markdown('<div class="approval"><div class="approval-label">本地标签建议</div><div class="approval-title">为 6 条收藏添加「产品灵感」标签</div><div class="approval-meta">不会修改 X 上的收藏或点赞状态</div></div>', unsafe_allow_html=True)
    cc1, cc2 = st.columns([1,5])
    with cc1: st.button("接受建议")
    with cc2: st.button("忽略", key="dismiss_tag")

