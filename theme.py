"""Dark theme derived from the light stylesheet, applied when the system prefers dark.

Each colour is mapped by the role of the property it sits in, so new light
styles get a dark counterpart automatically:
- text (color/fill/stroke): dark ink becomes light ink; near-white text stays
  (it sits on a coloured fill such as a blue button).
- backgrounds and borders: near-white surfaces and tints become dark
  surfaces, keeping a little of their hue; saturated fills are kept.
- shadows: become black with a stronger alpha; coloured glows keep their hue.
"""
import colorsys
import re

COLOR=re.compile(r'#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b')
TEXT=('color','fill','stroke','caret-color','text-decoration-color')
SHADOW=('box-shadow','text-shadow')
SURFACE=('background','background-color','border','border-top','border-right','border-bottom','border-left',
         'border-color','outline','outline-color','border-top-color','border-bottom-color')
# Exact values whose dark form is chosen by hand: page, surfaces and the translucent top bar.
FIXED={'#f5f7fb':'#0d1219','#f5f7fbef':'#0d1219ef','#fff':'#151c26','#ffffff':'#151c26','#14223b66':'#00000099'}
TOKENS=(':root{color-scheme:dark;--blue:#5b93f0;--blue-weak:#1a2840;--ink:#e3e9f2;--muted:#9aa8bc;'
        '--line:#263142;--surface:#151c26;--page:#0d1219}')

def parse(value):
    h=value[1:]
    if len(h)==3:h=''.join(c*2 for c in h)
    alpha=h[6:8] if len(h)==8 else ''
    return tuple(int(h[i:i+2],16)/255 for i in (0,2,4)),alpha

def fmt(rgb,alpha=''):
    return '#'+''.join('%02x'%round(max(0,min(1,c))*255) for c in rgb)+alpha

def text(value):
    rgb,alpha=parse(value);h,l,s=colorsys.rgb_to_hls(*rgb)
    if l>=0.9:return value
    l2=1-l*0.75
    accent=s>0.3 and l>0.3
    if accent:l2=max(min(l2,0.78),0.66)
    return fmt(colorsys.hls_to_rgb(h,l2,min(s,0.75) if accent else s*0.6),alpha)

def surface(value):
    key=value.lower()
    if key in FIXED:return FIXED[key]
    rgb,alpha=parse(value);h,l,s=colorsys.rgb_to_hls(*rgb)
    if l<0.78:return value
    l2=0.13+(1-l)*0.95
    return fmt(colorsys.hls_to_rgb(h,l2,s*0.45),alpha)

def shadow(value):
    rgb,alpha=parse(value);h,l,s=colorsys.rgb_to_hls(*rgb)
    a=int(alpha,16)/255 if alpha else 1
    if s>0.45 and 0.25<l<0.75:return fmt(colorsys.hls_to_rgb(h,max(l,0.55),s),'%02x'%round(min(1,a*1.6)*255))
    return '#000000%02x'%round(min(1,a*3+0.08)*255)

def declarations(body):
    out=[]
    for decl in body.split(';'):
        decl=re.sub(r'\bwhite\b','#fff',decl)
        if ':' not in decl or not COLOR.search(decl):continue
        name,value=decl.split(':',1);name=name.strip().lower()
        if name.startswith('--'):continue
        role=text if name in TEXT else shadow if name in SHADOW else surface if name in SURFACE or name.startswith('border') else None
        if role is None:continue
        changed=COLOR.sub(lambda m:role(m.group(0)),value)
        if changed!=value:out.append(name+':'+changed)
    return ';'.join(out)

def blocks(css):
    """Yield (prelude, body) pairs; body is a string for rules, a list for nested at-rules."""
    i=0;n=len(css)
    while i<n:
        j=css.find('{',i)
        if j<0:return
        prelude=css[i:j].strip();depth=1;k=j+1
        while k<n and depth:
            depth+={'{':1,'}':-1}.get(css[k],0);k+=1
        body=css[j+1:k-1]
        yield prelude,(list(blocks(body)) if prelude.startswith('@') else body)
        i=k

def render(items):
    out=''
    for prelude,body in items:
        if prelude.startswith('@'):
            if prelude.startswith(('@keyframes','@font-face')):continue
            inner=render(body)
            if inner:out+=prelude+'{'+inner+'}'
        else:
            d=declarations(body)
            if d:out+=prelude+'{'+d+'}'
    return out

def dark_css(css,tokens=False):
    """Return CSS that applies only under prefers-color-scheme: dark."""
    return '@media (prefers-color-scheme:dark){'+(TOKENS if tokens else '')+render(blocks(css))+'}'
