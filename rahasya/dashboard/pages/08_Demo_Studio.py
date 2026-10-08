"""An offline, reproducible walkthrough for the faculty presentation."""
import streamlit as st
from rahasya.brain.agents import AGENTS
from rahasya.brain.evidence import fact_view, rank_evidence, shortlist
from rahasya.core.models import Entity
from rahasya.dashboard import state
from rahasya.dashboard.demo import build_demo_result, load_demo
from rahasya.dashboard.ui import page_header, demo_notice, section

page_header("Demo studio", "One fictional case. The entire investigation workflow.", "Presentation workspace")
demo = build_demo_result()
demo_notice(demo)
left, right = st.columns([3, 1])
left.write("**Meet Alex Morgan.** A fictional student wants to understand which public profiles connect to their identity and what information deserves attention.")
if right.button("Load demo assessment", type="primary", width="stretch"):
    st.session_state.current_scan_id = load_demo(state.SCAN_STORE).scan_id
    st.success("Demo loaded. All investigation pages now show this fictional case.")
st.caption("Loading the demo preserves your other assessments. Switch back using the assessment selector.")

brief, pipeline, agents, guide = st.tabs(["01 · The story", "02 · Evidence pipeline", "03 · Agent team", "04 · Presenter guide"])
with brief:
    section("A small footprint. Several important questions.")
    steps = [
        ("01", "Discover", "An email and a public handle provide the starting point."),
        ("02", "Distinguish", "A linked project profile is supported. A matching forum handle is uncertain."),
        ("03", "Explain", "Every finding keeps its source, confidence, and review status."),
        ("04", "Act", "Review exposure and decide what information to make less public."),
    ]
    for column, (number, title, copy) in zip(st.columns(4), steps):
        column.markdown(f'<div class="stage"><span class="step">STEP {number}</span><b>{title}</b><p>{copy}</p></div>',
                        unsafe_allow_html=True)
    section("Open the case", "Load the demo first to use it across these pages")
    for col, label, page in zip(st.columns(3),
        ["Evidence explorer →", "Relationship map →", "Exposure report →"],
        ["pages/09_Evidence.py", "pages/02_CIA_Web.py", "pages/04_Exposure_Report.py"]):
        if col.button(label, width="stretch"):
            st.session_state.current_scan_id = load_demo(state.SCAN_STORE).scan_id
            st.switch_page(page)
    st.info("The profile, breach, and mention examples are fictional. The graph illustrates associations; it does not prove that every connected account belongs to one person.")

with pipeline:
    section("Different inputs. One evidence language.", "The model receives compact records")
    a, b = st.columns(2)
    with a:
        st.caption("ILLUSTRATIVE PROVIDER FORMATS")
        st.code('{"account": {"handle": "alex-builds", "url": "https://code.example/alex-builds"}}', language="json")
        st.code("site,username,status\nDemo Forum,alex-builds,found", language="csv")
    with b:
        st.caption("COMMON EVIDENCE CARD · FROM THE DEMO FIXTURE")
        st.json(fact_view(demo.entities[3]), expanded=False)
    st.caption("Each tool's Python adapter maps its own format to Entity records. These snippets illustrate that mapping; the LLM does not parse the raw files.")
    section("Try the reduction stage", "Synthetic data · real Python filtering · zero model calls")
    count = st.select_slider("Input observations", options=[100, 1000, 10000], value=10000)
    if st.button("Run evidence filtering", type="primary"):
        candidates = [Entity(id=f"fixture-{i}", entity_type="username", value=f"candidate-{i}",
                             normalized_value=f"candidate-{i}", source_module="Synthetic fixture", confidence=.3)
                      for i in range(count - 1)]
        strong = Entity(id="strong-fixture", entity_type="username", value="alex-builds",
                        normalized_value="alex-builds", source_module="Synthetic fixture",
                        confidence=.95, source_reliability="high", is_ground_truth=True,
                        evidence_urls=["https://code.example/alex-builds"])
        candidates.append(strong)
        chosen, counts = shortlist(candidates, [], 20, demo.entities[1])
        reviewed = rank_evidence(chosen, 5, demo.entities[1])
        st.session_state.demo_reduction = {"counts": counts, "cards": [fact_view(e) for e in reviewed],
                                          "strong_selected": strong.id in {e.id for e in reviewed}}
    if "demo_reduction" in st.session_state:
        reduction = st.session_state.demo_reduction
        metrics = st.columns(4)
        for col, label, value in zip(metrics, ["Input records", "Working candidates", "Review shortlist", "Model calls"],
            [reduction["counts"]["received"], reduction["counts"]["selected"], len(reduction["cards"]), 0]):
            col.metric(label, f"{value:,}")
        st.success("The source-backed record placed last was retained in the review shortlist."
                   if reduction["strong_selected"] else "The source-backed record was not retained.")
        st.caption("These are filtering results, not model assessments. In an actual agentic scan, deferred observations remain archived; this lab does not persist a bulk archive.")
        st.dataframe([{"Value": c["value"], "Source": c["source"], "Source-attested": c["source_attested"]}
                      for c in reduction["cards"]], hide_index=True, width="stretch")
with agents:
    section("Eight roles. One shared local model.", "Architecture overview · no inference in this demo")
    st.write("The coordinator assigns a task; a specialist selects an allowed module; the reviewer assesses observations; the reporter explains supported findings.")
    columns = st.columns(2)
    for i, agent in enumerate(AGENTS):
        with columns[i % 2].container(border=True):
            st.subheader(agent.role.value.replace("_", " ").title())
            st.caption(agent.assignment)
            st.write(", ".join(agent.modules) if agent.modules else "Planning, review, or reporting role")
    st.caption("The configured runtime uses local Ollama models. The demo's decisions are prepared examples and do not measure model accuracy or speed.")
with guide:
    section("Your 8-minute walkthrough", "A presentation you can repeat")
    st.dataframe([
        {"Time": "0–1 min", "Show": "Overview", "Explain": "A person audits their own digital footprint within an agreed scope."},
        {"Time": "1–2 min", "Show": "Evidence explorer", "Explain": "Source-backed findings and uncertain matches are different."},
        {"Time": "2–3 min", "Show": "Relationship map", "Explain": "Follow a connection and inspect the underlying evidence."},
        {"Time": "3–4 min", "Show": "Evidence pipeline", "Explain": "Python reduces large, inconsistent outputs before AI review."},
        {"Time": "4–5 min", "Show": "AI agents", "Explain": "Restricted roles select tools and leave an inspectable activity trail."},
        {"Time": "5–6 min", "Show": "Timeline and source log", "Explain": "Dates, gaps, and source failures remain visible."},
        {"Time": "6–8 min", "Show": "Exposure report and export", "Explain": "Review the rubric, limitations, and practical next actions."},
    ], hide_index=True, width="stretch")
    st.info("Presentation boundary: this case is synthetic. Live assessments require the appropriate authorization and source permissions. Model judgments are advisory; the local prototype is not a compliance certification.")
    st.caption("Current limitations: heuristic filtering can defer relevant observations; PostgreSQL integration and crash recovery remain future work.")
