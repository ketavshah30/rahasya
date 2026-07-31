import streamlit as st

def render_risk_meter(score: int, label: str) -> str:
    """
    Renders an animated risk meter using pure HTML/CSS.
    Returns the HTML string to be rendered with st.components.v1.html
    """
    # Clamp score
    score = max(0, min(100, score))
    
    # Calculate colors based on score
    if score < 33:
        color = "#10b981"  # green
    elif score < 66:
        color = "#f59e0b"  # amber
    else:
        color = "#ef4444"  # red
        
    rotation = (score / 100.0) * 180 - 90  # -90 to 90 degrees
    
    html = f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Rajdhani:wght@600&display=swap');
    .meter-container {{
        width: 200px;
        height: 120px;
        position: relative;
        overflow: hidden;
        margin: 0 auto;
        font-family: 'Rajdhani', sans-serif;
    }}
    .meter-background {{
        width: 200px;
        height: 200px;
        border-radius: 50%;
        background: conic-gradient(from 270deg, #10b981 0%, #f59e0b 25%, #ef4444 50%, transparent 50%);
        position: absolute;
        top: 0;
        left: 0;
    }}
    .meter-cover {{
        width: 160px;
        height: 160px;
        border-radius: 50%;
        background-color: #0a0e17; /* match app background */
        position: absolute;
        top: 20px;
        left: 20px;
        display: flex;
        flex-direction: column;
        align-items: center;
        padding-top: 40px;
        box-sizing: border-box;
    }}
    .needle {{
        width: 4px;
        height: 90px;
        background-color: #e2e8f0;
        position: absolute;
        bottom: 0px;
        left: 98px;
        transform-origin: bottom center;
        transform: rotate({rotation}deg);
        transition: transform 1s cubic-bezier(0.4, 0, 0.2, 1);
        border-radius: 2px 2px 0 0;
    }}
    .needle::after {{
        content: '';
        width: 16px;
        height: 16px;
        background-color: #e2e8f0;
        border-radius: 50%;
        position: absolute;
        bottom: -8px;
        left: -6px;
    }}
    .score-display {{
        color: {color};
        font-size: 32px;
        font-weight: 600;
        text-shadow: 0 0 10px {color}80;
        margin-top: 10px;
    }}
    .label-display {{
        color: #64748b;
        font-size: 14px;
        text-transform: uppercase;
        letter-spacing: 1px;
    }}
    </style>
    <div class="meter-container">
        <div class="meter-background"></div>
        <div class="meter-cover">
            <div class="score-display">{score}</div>
            <div class="label-display">{label}</div>
        </div>
        <div class="needle"></div>
    </div>
    """
    return html
