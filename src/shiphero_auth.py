"""Exchanges a long-lived ShipHero refresh token for a short-lived access token.

Per ShipHero's docs (https://developer.shiphero.com/getting-started/):
  POST https://public-api.shiphero.com/auth/refresh
  body: {"refresh_token": "..."}
  -> {"access_token": "...", "expires_in": 2419200, ...}

The refresh token itself is not rotated by this call, so it's safe to store
once (e.g. as a GitHub secret) and reuse indefinitely, until it's revoked.
"""
import requests


class ShipHeroAuthError(RuntimeError):
    pass


def get_access_token(refresh_token: str, auth_url: str) -> str:
    resp = requests.post(
        auth_url,
        json={"refresh_token": refresh_token},
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise ShipHeroAuthError(
            f"ShipHero token refresh failed ({resp.status_code}): {resp.text}"
        )
    data = resp.json()
    access_token = data.get("access_token")
    if not access_token:
        raise ShipHeroAuthError(f"No access_token in refresh response: {data}")
    return access_token
