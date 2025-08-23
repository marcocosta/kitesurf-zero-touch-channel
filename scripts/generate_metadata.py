
import json, pathlib, datetime

template = json.load(open("templates/metadata.json", "r", encoding="utf-8"))
today = datetime.date.today().isoformat()
data = {
    "title_en": template["title_en"].format(location="Northeast Brazil Coast"),
    "title_pt": template["title_pt"].format(location="Litoral Nordeste do Brasil"),
    "description_en": template["description_en"].format(location="Northeast Brazil Coast", wind_knots="18–24", tide_state="mid", credits="See credits file in assets folder.", music_track="Lo-Fi Breeze"),
    "description_pt": template["description_pt"].format(location="Litoral Nordeste do Brasil", wind_knots="18–24", tide_state="média", credits="Ver arquivo de créditos na pasta de assets.", music_track="Lo-Fi Breeze"),
    "tags": template["tags"],
    "chapters": template["chapters"],
    "scheduled_date": today,
}
out = pathlib.Path("content/uploads/metadata_001.json")
out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
print("Saved", out)
