"""NAS-only recording queries: call types, filters, sorting, statistics and XLSX export.

RecType labels come from PhoneRec.exe (switch at 0x45aed2, table 0x45b89c):
1 去電錄音, 2 來電錄音, 3 音頻錄音, 4 來電未接, 5 去電未錄音, 6 來電未錄音, 7 自動回應.
"""
import datetime as dt
import io
import zipfile
from xml.sax.saxutils import escape

REC_TYPES={1:'去電錄音',2:'來電錄音',3:'音頻錄音',4:'來電未接',5:'去電未錄音',6:'來電未錄音',7:'自動回應'}
CALL_TYPES={'ALL':('全部',None),'RECORDED':('有錄音',(1,2,3,7)),'MISSED':('來電未接',(4,)),
            'UNRECORDED':('有通話未錄音',(5,6)),'AUTO':('自動回應',(7,))}
SORTS={'time':('時間（舊到新）',"json_extract(row_json,'$.timestamp'),day,key"),
       'time_desc':('時間（新到舊）',"json_extract(row_json,'$.timestamp') DESC,day,key"),
       'length_desc':('秒數（長到短）',"coalesce(json_extract(row_json,'$.RecTime'),0) DESC,json_extract(row_json,'$.timestamp'),key"),
       'phone':('電話',"coalesce(json_extract(row_json,'$.PhoneNumber'),''),json_extract(row_json,'$.timestamp'),key")}
PERIODS={'day':'按天','week':'按週','month':'按月','quarter':'按季','year':'按年'}
TS="json_extract(row_json,'$.timestamp')"
TYPE="coalesce(json_extract(row_json,'$.RecType'),0)"

def type_label(row):return REC_TYPES.get(row.get('RecType'),'未知類型')

def where_clause(start='',end='',phone='',direction='ALL',call_type='ALL',line=0,min_seconds=None,max_seconds=None):
    if direction not in ('ALL','IN','OUT','UNKNOWN'):raise ValueError('無效來去電條件')
    if call_type not in CALL_TYPES:raise ValueError('無效通話類型')
    if line not in (0,1,2,3,4):raise ValueError('無效線路')
    for value in (min_seconds,max_seconds):
        if value is not None and not 0<=value<=86400:raise ValueError('秒數須在 0～86400 之間')
    if min_seconds is not None and max_seconds is not None and min_seconds>max_seconds:raise ValueError('最短秒數不可大於最長秒數')
    clauses=[];args=[]
    for value,operator in [(start,'>='),(end,'<=')]:
        if value:
            parsed=dt.datetime.fromisoformat(value)
            if parsed.tzinfo is not None:raise ValueError('請使用台北當地時間')
            clauses.extend(['day '+operator+' ?',TS+' '+operator+' ?']);args.extend([parsed.date().isoformat(),parsed.isoformat(timespec='seconds')])
    if start and end and dt.datetime.fromisoformat(start)>dt.datetime.fromisoformat(end):raise ValueError('開始時間不可晚於結束時間')
    if phone:clauses.append("instr(coalesce(json_extract(row_json,'$.PhoneNumber'),''),?)>0");args.append(phone)
    if direction!='ALL':clauses.append("coalesce(json_extract(row_json,'$.direction'),'未知')=?");args.append({'IN':'撥入','OUT':'撥出','UNKNOWN':'未知'}[direction])
    codes=CALL_TYPES[call_type][1]
    if codes:clauses.append(TYPE+' IN ('+','.join('?'*len(codes))+')');args.extend(codes)
    if line:clauses.append("json_extract(row_json,'$.RecChannel')=?");args.append(line-1)
    if min_seconds is not None:clauses.append("coalesce(json_extract(row_json,'$.RecTime'),0)>=?");args.append(min_seconds)
    if max_seconds is not None:clauses.append("coalesce(json_extract(row_json,'$.RecTime'),0)<=?");args.append(max_seconds)
    return (' WHERE '+' AND '.join(clauses) if clauses else ''),args

def period_key(period):
    if period=='day':return 'substr('+TS+',1,10)'
    if period=='week':return 'date('+TS+",'-6 days','weekday 1')"
    if period=='month':return 'substr('+TS+',1,7)'
    if period=='quarter':return 'substr('+TS+",1,4)||'-Q'||((cast(substr("+TS+',6,2) as integer)+2)/3)'
    if period=='year':return 'substr('+TS+',1,4)'
    raise ValueError('無效統計週期')

def statistics(db,where,args,period):
    key=period_key(period)
    sql=('SELECT '+key+' AS period,count(*) AS total,'
         'sum(CASE WHEN '+TYPE+' IN (2,7) THEN 1 ELSE 0 END) AS incoming,'
         'sum(CASE WHEN '+TYPE+'=1 THEN 1 ELSE 0 END) AS outgoing,'
         'sum(CASE WHEN '+TYPE+'=4 THEN 1 ELSE 0 END) AS missed,'
         'sum(CASE WHEN '+TYPE+' IN (5,6) THEN 1 ELSE 0 END) AS unrecorded,'
         'sum(CASE WHEN '+TYPE+'=7 THEN 1 ELSE 0 END) AS auto,'
         "coalesce(sum(json_extract(row_json,'$.RecTime')),0) AS seconds "
         'FROM recordings'+where+' GROUP BY period ORDER BY period DESC')
    rows=[dict(r) for r in db.execute(sql,args).fetchall()]
    totals={k:sum(r[k] for r in rows) for k in ('total','incoming','outgoing','missed','unrecorded','auto','seconds')}
    return dict(rows=rows,totals=totals,period=period)

def duration(seconds):
    seconds=int(seconds or 0);h,rem=divmod(seconds,3600);m,s=divmod(rem,60)
    return '%d:%02d:%02d'%(h,m,s)

def _cell(ref,value):
    if isinstance(value,(int,float)) and not isinstance(value,bool):return '<c r="%s"><v>%s</v></c>'%(ref,value)
    text=escape(''.join(ch for ch in str(value) if ch in '\t\n\r' or ord(ch)>=32))
    return '<c r="%s" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>'%(ref,text)

def column(n):
    out=''
    while n:n,r=divmod(n-1,26);out=chr(65+r)+out
    return out

def xlsx(header,rows,sheet='錄音'):
    """Minimal standard XLSX with inline strings, so phone numbers keep leading zeros."""
    lines=[]
    for y,values in enumerate([header]+list(rows),1):
        lines.append('<row r="%d">%s</row>'%(y,''.join(_cell(column(x)+str(y),v) for x,v in enumerate(values,1))))
    widths=''.join('<col min="%d" max="%d" width="%d" customWidth="1"/>'%(i,i,w) for i,w in enumerate([20,6,8,12,16,10,10,8,12],1))
    files={
        '[Content_Types].xml':'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        '_rels/.rels':'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        'xl/workbook.xml':'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="%s" sheetId="1" r:id="rId1"/></sheets></workbook>'%escape(sheet),
        'xl/_rels/workbook.xml.rels':'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        'xl/worksheets/sheet1.xml':'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols>'+widths+'</cols><sheetData>'+''.join(lines)+'</sheetData></worksheet>',
    }
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        for name,text in files.items():z.writestr(name,text.encode())
    return out.getvalue()

EXPORT_HEADER=['時間','線路','來去電','類型','電話','錄音秒數','通話秒數','響鈴次數','備份狀態']
def export_row(row):
    status='已備份' if row['backed_up'] else '已略過' if row['skipped'] else '待重試' if row['error'] else '等待備份' if row.get('has_audio') else '無音檔'
    return [row['timestamp'].replace('T',' '),row['RecChannel']+1,row['direction'],type_label(row),row['PhoneNumber'] or '',
            int(row.get('RecTime') or 0),int(row.get('TalkTime') or 0),int(row.get('RingTime') or 0),status]
