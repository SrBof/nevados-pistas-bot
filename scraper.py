import json
import os
import re
import sys
import pathlib

import requests
from bs4 import BeautifulSoup

try:
    sys.stdout.reconfigure(encoding="utf-8")  # evita UnicodeEncodeError en consola Windows
except Exception:
    pass

URL = "https://www.nevadosdechillan.com/reporte-montana"
STATE_FILE = pathlib.Path(__file__).parent / "estado.json"
MIN_ITEMS_ESPERADOS = 30  # ~40 reales; si baja de esto, la web cambió de estructura

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")


def normaliza_estado(texto: str) -> str:
    t = (texto or "").strip().lower()
    if t.startswith("abiert"):
        return "abierto"
    if t.startswith("cerrad"):
        return "cerrado"
    return "desconocido"


def scrape() -> tuple[dict, str]:
    r = requests.get(URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    soup = BeautifulSoup(r.text, "html.parser")

    items: dict[str, str] = {}

    for a in soup.select(".andarivel"):
        ps = a.select("p")
        nombre = ps[0].get_text(strip=True) if ps else None
        etiqueta = a.select_one(".etiqueta")
        if nombre and etiqueta:
            items[f"Andarivel · {nombre}"] = normaliza_estado(etiqueta.get_text())

    for tabla in soup.select("table.tablaSimple"):
        for tr in tabla.select("tbody tr"):
            tds = tr.find_all("td")
            if len(tds) >= 4:
                nombre = tds[0].get_text(strip=True)
                etiqueta = tds[3].select_one(".etiqueta")
                estado = normaliza_estado(etiqueta.get_text()) if etiqueta else "desconocido"
                if nombre:
                    items[f"Pista · {nombre}"] = estado

    texto_completo = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    m = re.search(
        r"[Úú]lt\.?\s*actualizaci[oó]n:\s*(\d{2}-\d{2}-\d{4}\s+\d{1,2}:\d{2}\s*[ap]\.?m\.?)",
        texto_completo,
        re.I,
    )
    ts = m.group(1).strip() if m else ""

    return items, ts


def cargar_estado() -> dict | None:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return None


def guardar_estado(items: dict, ts: str) -> None:
    STATE_FILE.write_text(
        json.dumps({"items": items, "ts": ts}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def notificar(texto: str) -> None:
    if not TG_TOKEN or not TG_CHAT:
        print("[WARN] Sin credenciales de Telegram. Mensaje que se habría enviado:\n")
        print(texto)
        return
    resp = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={
            "chat_id": TG_CHAT,
            "text": texto,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    resp.raise_for_status()


def diff_estados(viejo: dict, nuevo: dict) -> list[tuple[str, str, str]]:
    cambios = []
    for k, v in nuevo.items():
        antes = viejo.get(k)
        if antes != v:
            cambios.append((k, antes or "nuevo", v))
    for k, v in viejo.items():
        if k not in nuevo:
            cambios.append((k, v, "removido"))
    return cambios


def construir_mensaje(cambios: list, ts: str) -> str:
    aperturas = [c for c in cambios if c[2] == "abierto"]
    cierres = [c for c in cambios if c[2] == "cerrado"]
    otros = [c for c in cambios if c[2] not in ("abierto", "cerrado")]

    lineas = ["<b>Nevados de Chillán — cambio en el reporte</b>", ""]
    if aperturas:
        lineas.append("🟢 <b>ABIERTO:</b>")
        lineas += [f"  • {k.split(' · ', 1)[-1]}  ({k.split(' · ')[0]})" for k, _, _ in aperturas]
        lineas.append("")
    if cierres:
        lineas.append("🔴 <b>Cerrado:</b>")
        lineas += [f"  • {k.split(' · ', 1)[-1]}" for k, _, _ in cierres]
        lineas.append("")
    if otros:
        lineas.append("❓ <b>Estado inesperado (revisar):</b>")
        lineas += [f"  • {k}: {a} → {n}" for k, a, n in otros]
        lineas.append("")
    if ts:
        lineas.append(f"🕒 {ts}")
    lineas.append('<a href="https://www.nevadosdechillan.com/reporte-montana">Ver reporte</a>')
    return "\n".join(lineas)


def main() -> None:
    items, ts = scrape()

    if len(items) < MIN_ITEMS_ESPERADOS:
        notificar(
            f"⚠️ El monitor de Nevados solo encontró {len(items)} elementos "
            f"(se esperaban ~40). La estructura de la web pudo cambiar; el bot no actualizó su estado."
        )
        sys.exit(1)

    estado = cargar_estado()

    if estado is None:
        guardar_estado(items, ts)
        abiertos = sum(1 for v in items.values() if v == "abierto")
        print(f"Baseline creado: {len(items)} elementos, {abiertos} abiertos. Sin notificar (primer run).")
        return

    cambios = diff_estados(estado.get("items", {}), items)
    if cambios:
        notificar(construir_mensaje(cambios, ts))
        print(f"{len(cambios)} cambio(s) notificado(s).")
    else:
        print(f"Sin cambios. ({ts})")

    guardar_estado(items, ts)


if __name__ == "__main__":
    main()
