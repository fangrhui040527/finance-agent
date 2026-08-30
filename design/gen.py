from pathlib import Path
CSS = Path("_css.txt").read_text()

I = {  # 16px stroke icons, one consistent style
 "ask":'<path d="M8 2 2 5v6l6 3 6-3V5L8 2Z"/><path d="M8 8v6M2 5l6 3 6-3"/>',
 "why":'<path d="M2 12l3.5-4 2.5 2.5L14 4"/><path d="M14 8V4h-4"/>',
 "prices":'<path d="M2 13h12"/><path d="M4 11V7M7 11V4M10 11V8M13 11V5"/>',
 "thesis":'<path d="M4 2h6l3 3v9H4V2Z"/><path d="M10 2v3h3M6 8h4M6 11h3"/>',
 "port":'<circle cx="8" cy="8" r="5.5"/><path d="M8 2.5v5.5l3.9 3.9"/>',
 "size":'<path d="M2 8h12"/><path d="M5 5 2 8l3 3M11 5l3 3-3 3"/>',
 "pred":'<path d="M2 8h3l2-4 2 8 2-4h3"/>',
 "trace":'<circle cx="4" cy="4" r="1.8"/><circle cx="12" cy="8" r="1.8"/><circle cx="6" cy="12" r="1.8"/><path d="M5.5 5.2 10.4 7M10.6 9.2 7.3 11.1"/>',
 "learn":'<path d="M2 4.5 8 2l6 2.5L8 7 2 4.5Z"/><path d="M4.5 6v4c0 1 1.7 2 3.5 2s3.5-1 3.5-2V6"/>',
 "set":'<circle cx="8" cy="8" r="2.2"/><path d="M8 1.5v2M8 12.5v2M14.5 8h-2M3.5 8h-2M12.6 3.4l-1.4 1.4M4.8 11.2l-1.4 1.4M12.6 12.6l-1.4-1.4M4.8 4.8 3.4 3.4"/>',
}
NAV = [("ask","Ask","ask"),("why","Why it moved","why"),("prices","Prices","prices"),
       ("thesis","Thesis","thesis"),("port","Portfolio","port"),("size","Sizing","size"),
       ("pred","Predictions","pred"),("trace","Trace","trace"),
       ("learn","Learn","learn"),("set","Settings","set")]

def rail(active):
    out=['<div class="rail">',
         '<div class="brand"><span>FinPlanet</span><b>Analyst Mind</b></div>']
    for key,label,icon in NAV:
        if key=="learn": out.append('<div class="railsep"></div>')
        on=" on" if key==active else ""
        out.append(f'<a class="nav{on}"><svg viewBox="0 0 16 16">{I[icon]}</svg>{label}</a>')
    out.append('</div>')
    return "\n".join(out)

def page(name, active, body, logic=None, props=None, w=1280, h=900):
    props = props or {}
    props.setdefault("$preview", {"width": w, "height": h})
    import json
    pj = json.dumps(props).replace("&","&amp;").replace("'","&#39;")
    script = ""
    if logic is not None:
        script = f"<script data-dc-script data-props='{pj}'>\n{logic}\n</script>"
    else:
        script = ("<script data-dc-script data-props='%s'>\n"
                  "class Component extends DCLogic {}\n</script>" % pj)
    Path(f"{name}.dc.html").write_text(f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <script src="./support.js"></script>
</head>
<body>
<x-dc>
<helmet>
  <style>
{CSS}
  </style>
</helmet>
<div class="app" style="width:{w}px;min-height:{h}px">
{rail(active)}
<div class="main">
{body}
</div>
</div>
</x-dc>
{script}
</body>
</html>
""")
    print(f"  wrote {name}.dc.html")
