from __future__ import annotations

def text_color(background: str) -> str:
    value=background.lstrip("#")
    if len(value)!=6: return "#222222"
    r,g,b=(int(value[i:i+2],16) for i in (0,2,4))
    return "#222222" if (r*299+g*587+b*114)/1000 > 150 else "#FFFFFF"
