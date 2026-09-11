#!/usr/bin/env python3
"""
Surveillance de stock generique (multi-produits).

Verifie tous les produits listes dans products.json et envoie une alerte
Telegram des qu'un produit passe de "rupture de stock" a "disponible"
(un seul message par produit, pas de spam a chaque execution).

Pour surveiller un nouveau produit : ajoute une entree dans products.json
(id, name, url, price) puis commit/push. Aucun nouveau compte, bot Telegram
ou secret n'est necessaire - tout est deja en place et reutilise.

Ne se connecte a aucun compte, ne remplit aucun panier, n'entre aucune
information de paiement. Il envoie juste un lien direct des qu'un produit
est disponible - a toi de finaliser l'achat.

Variables d'environnement requises (deja definies en secrets GitHub) :
  TELEGRAM_BOT_TOKEN : token du bot obtenu via @BotFather
  TELEGRAM_CHAT_ID   : ton chat_id Telegram
"""

import json
import os
import sys
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

PRODUCTS_FILE = Path(__file__).parent / "products.json"
STATE_FILE = Path(__file__).parent / "state.json"

DEFAULT_OUT_OF_STOCK_MARKERS = ["épuisé en ligne", "rupture de stock", "indisponible en ligne"]
DEFAULT_IN_STOCK_MARKERS = ["ajouter au panier"]


def load_products() -> list:
    return json.loads(PRODUCTS_FILE.read_text())


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def send_telegram_message(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID manquant - notification non envoyee.")
        return
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": False},
        timeout=15,
    )
    if not resp.ok:
        print(f"Echec envoi Telegram: {resp.status_code} {resp.text}")


def check_product(page, product: dict) -> str:
    out_markers = [m.lower() for m in product.get("out_of_stock_markers", DEFAULT_OUT_OF_STOCK_MARKERS)]
    in_markers = [m.lower() for m in product.get("in_stock_markers", DEFAULT_IN_STOCK_MARKERS)]

    page.goto(product["url"], wait_until="domcontentloaded", timeout=30000)

    # Ferme un eventuel bandeau cookies qui pourrait bloquer le rendu.
    for label in ["Tout accepter", "Accepter", "J'accepte", "Accept all"]:
        try:
            btn = page.get_by_role("button", name=label, exact=False)
            if btn.count() > 0:
                btn.first.click(timeout=2000)
                break
        except Exception:
            pass

    # Attend activement l'apparition d'un des marqueurs de stock plutot
    # qu'un simple sleep fixe (certains sites sont lents a s'hydrater
    # depuis des datacenters cloud).
    deadline_ms = 25000
    poll_interval_ms = 1000
    elapsed = 0
    text = ""
    while elapsed < deadline_ms:
        text = page.inner_text("body").lower()
        if any(m in text for m in out_markers + in_markers):
            break
        page.wait_for_timeout(poll_interval_ms)
        elapsed += poll_interval_ms

    has_oos = any(m in text for m in out_markers)
    has_in = any(m in text for m in in_markers)

    if has_oos and not has_in:
        return "out_of_stock"
    if has_in and not has_oos:
        return "in_stock"
    return "unknown"


def main() -> int:
    products = load_products()
    state = load_state()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            locale="fr-FR",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            ),
            extra_http_headers={"Accept-Language": "fr-FR,fr;q=0.9"},
        )
        page = context.new_page()

        for product in products:
            pid = product["id"]
            last_status = state.get(pid, "out_of_stock")

            try:
                current_status = check_product(page, product)
            except Exception as exc:
                print(f"[{pid}] Erreur de chargement: {exc}")
                continue

            print(f"[{pid}] Statut precedent: {last_status} | Statut actuel: {current_status}")

            if current_status == "in_stock" and last_status != "in_stock":
                price_txt = f" ({product['price']})" if product.get("price") else ""
                send_telegram_message(
                    "ALERTE STOCK\n"
                    f"{product['name']}{price_txt} est disponible !\n\n"
                    f"{product['url']}\n\n"
                    "Vas-y vite, ajoute-le au panier et finalise l'achat toi-meme."
                )
                print(f"[{pid}] Notification envoyee.")
            elif current_status == "unknown":
                print(f"[{pid}] Statut ambigu, pas de notification envoyee.")

            if current_status != "unknown":
                state[pid] = current_status

        browser.close()

    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
