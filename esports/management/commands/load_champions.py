import json
from pathlib import Path

import requests
from django.core.management.base import BaseCommand, CommandError

from esports.models import Champion

ROLES_FILE = Path(__file__).resolve().parents[2] / "data" / "champion_roles.json"
DDRAGON = "https://ddragon.leagueoflegends.com"


def bundled_roles():
    data = json.loads(ROLES_FILE.read_text(encoding="utf-8"))
    data.pop("_comment", None)
    return data


class Command(BaseCommand):
    help = (
        "Load champions. By default uses the bundled list (offline). "
        "With --ddragon, also pulls every champion + portrait URLs from Riot Data Dragon."
    )

    def add_arguments(self, parser):
        parser.add_argument("--ddragon", action="store_true", help="Fetch the full list and images from Data Dragon")
        parser.add_argument("--ddragon-version", dest="dd_version", help="Data Dragon version (default: latest)")

    def handle(self, *args, ddragon=False, dd_version=None, **opts):
        version = dd_version
        roles = bundled_roles()
        created = updated = 0

        if ddragon:
            try:
                version = version or requests.get(f"{DDRAGON}/api/versions.json", timeout=20).json()[0]
                resp = requests.get(f"{DDRAGON}/cdn/{version}/data/en_US/champion.json", timeout=30)
                resp.raise_for_status()
            except requests.RequestException as exc:
                raise CommandError(f"Data Dragon request failed: {exc}") from exc
            entries = {
                key: (info["name"], f"{DDRAGON}/cdn/{version}/img/champion/{info['image']['full']}")
                for key, info in resp.json()["data"].items()
            }
            self.stdout.write(f"Data Dragon {version}: {len(entries)} champions")
        else:
            entries = {key: (name, "") for key, (name, _roles) in roles.items()}

        for key, (name, image_url) in entries.items():
            defaults = {"name": name, "roles": roles.get(key, [None, []])[1]}
            if image_url:
                defaults["image_url"] = image_url
            obj, was_created = Champion.objects.get_or_create(key=key, defaults=defaults)
            if was_created:
                created += 1
                continue
            changed = False
            if image_url and obj.image_url != image_url:
                obj.image_url, changed = image_url, True
            if not obj.roles and defaults["roles"]:
                obj.roles, changed = defaults["roles"], True
            if changed:
                obj.save()
                updated += 1

        missing_roles = Champion.objects.filter(roles=[]).count()
        self.stdout.write(self.style.SUCCESS(f"Champions: {created} created, {updated} updated."))
        if missing_roles:
            self.stdout.write(
                self.style.WARNING(
                    f"{missing_roles} champions have no roles yet; add them to champion_roles.json or run derive_roles."
                )
            )
