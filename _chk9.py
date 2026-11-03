import json,urllib.request,sys
sys.stdout.reconfigure(encoding="utf-8")
k="AIzaSyBSpsVlxin2qHaGKeC682LDnP_n5HVz0yc"
ids=["1k0m72NSUPE","SKUw826EVR8","tZpOxePId4Q","5EFVJ36f9mY","--CRVBXVH4E","0-yPYKjdtNg"]
u="https://www.googleapis.com/youtube/v3/videos?part=snippet&id="+",".join(ids)+"&key="+k
d=json.load(urllib.request.urlopen(u))
for it in d["items"]:
    s=it["snippet"]
    print(it["id"],"|",s["channelId"],"|",s["title"][:90],"|",s.get("publishedAt","")[:10])
