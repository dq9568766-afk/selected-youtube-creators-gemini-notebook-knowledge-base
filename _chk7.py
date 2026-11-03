import json,urllib.request,sys
sys.stdout.reconfigure(encoding="utf-8")
k="AIzaSyBSpsVlxin2qHaGKeC682LDnP_n5HVz0yc"
ids=["n_b8j78Ft6E","Dn9RKM_SaeQ","Ufcy3Laj_s0"]
u="https://www.googleapis.com/youtube/v3/videos?part=snippet&id="+",".join(ids)+"&key="+k
d=json.load(urllib.request.urlopen(u))
for it in d["items"]:
    s=it["snippet"]
    print(it["id"],"|",s["channelId"],"|",s["title"])
    print("    localized:", json.dumps(s.get("localizations",{}),ensure_ascii=False)[:200])
