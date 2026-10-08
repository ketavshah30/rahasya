"""Shared visual language. Escape data before embedding it in presentation HTML."""
from html import escape
from pathlib import Path
import streamlit as st

def load_theme():
    css = Path(__file__).with_name("static").joinpath("style.css").read_text(encoding="utf-8")
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)

def page_header(title, subtitle, eyebrow="Investigation workspace"):
    load_theme()
    st.markdown(f'<div class="eyebrow">{escape(eyebrow)}</div>', unsafe_allow_html=True)
    st.title(title)
    st.caption(subtitle)

def section(title, note=""):
    st.markdown(f'<div class="section-label"><h2>{escape(title)}</h2><span>{escape(note)}</span></div>',
                unsafe_allow_html=True)

def is_demo(result):
    return bool(result and result.scan_id.startswith("demo-") and result.entities and
                all(e.metadata.get("synthetic_demo") is True for e in result.entities))

def demo_notice(result):
    if is_demo(result):
        st.markdown('<div class="demo-banner"><strong>SYNTHETIC DEMO</strong> &nbsp; '
                    'Fictional identity, prepared evidence and illustrative agent decisions. '
                    'No external search or model inference was performed.</div>', unsafe_allow_html=True)

def target_name(result):
    request = result.request if result else None
    return next((getattr(request, field, None) for field in ("name", "username", "email", "phone")
                 if getattr(request, field, None)), "Untitled assessment")

def evidence_rows(entities):
    rows = []
    for entity in entities:
        review = entity.metadata.get("agent_review", "unreviewed")
        dot = "status-indicator" + (" uncertain" if review != "supported" else "")
        rows.append(f'<div class="evidence-row"><span class="{dot}"></span><div>'
                    f'<b>{escape(str(entity.value)[:75])}</b><small>{escape(entity.source_module)} · '
                    f'{escape(entity.entity_type.value.replace("_", " "))}</small></div>'
                    f'<span class="right">{escape(review.upper())}</span></div>')
    st.markdown("".join(rows), unsafe_allow_html=True)

def footer():
    st.markdown('<div class="footer"><span>RAHASYA · Digital footprint intelligence</span>'
                '<span>Evidence first. Human reviewed.</span></div>', unsafe_allow_html=True)

def hero():
    st.markdown('''<div class="hero"><div><div class="eyebrow">DIGITAL FOOTPRINT INTELLIGENCE</div>
<h1>Your footprint.<br><span class="accent">In focus.</span></h1>
<p>Turn scattered online signals into a clear, traceable picture. Explore connections, review the evidence, and understand your exposure.</p>
<div class="hero-note"><span><b>◈</b> Evidence-led analysis</span><span><b>◈</b> Local AI architecture</span></div></div>
<svg viewBox="0 0 500 300" role="img" aria-label="Illustration of connected identifiers, not investigation data" xmlns="http://www.w3.org/2000/svg">
<defs><pattern id="dots" width="22" height="22" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r="1" fill="#315146"/></pattern></defs>
<rect width="500" height="300" fill="url(#dots)" opacity=".6"/>
<g fill="none" stroke="#436b63"><circle cx="258" cy="146" r="103" stroke-dasharray="3 7"/>
<path d="M258 146L122 77M258 146L399 65M258 146L420 201M258 146L159 244M258 146L58 164"/></g>
<circle cx="258" cy="146" r="50" fill="#16382f" stroke="#619f8a"/><circle cx="258" cy="146" r="38" fill="#244b3f" stroke="#89e4c3"/>
<g stroke="#b2f7df" fill="none" stroke-width="2"><circle cx="258" cy="137" r="9"/><path d="M241 164c0-17 34-17 34 0"/></g>
<g fill="#142c29" stroke="#71c8ac"><rect x="58" y="53" width="128" height="44" rx="10"/><rect x="344" y="43" width="119" height="44" rx="10"/><rect x="355" y="180" width="130" height="44" rx="10"/><rect x="95" y="224" width="132" height="44" rx="10"/><rect x="8" y="144" width="105" height="42" rx="10"/></g>
<g fill="#b6dfd1" font-family="Segoe UI,sans-serif" font-size="12" text-anchor="middle"><text x="122" y="80">@ public handle</text><text x="403" y="70">◈ profile</text><text x="420" y="207">↗ source evidence</text><text x="161" y="251">✉ email address</text><text x="60" y="170">◷ timeline</text><text x="258" y="214" font-size="10" fill="#81a99a">CONNECTIONS, WITH CONTEXT</text></g></svg></div>''', unsafe_allow_html=True)
