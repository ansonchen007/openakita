from __future__ import annotations

import pytest

from openakita.skills.marketplace import (
    build_skillhub_download_url,
    normalize_skillhub_response,
    normalize_skillhub_source,
    parse_skillhub_locator,
    resolve_marketplace_install_source,
)


def test_skillhub_payload_is_normalized_to_provider_neutral_model() -> None:
    payload = {
        "code": 0,
        "message": "success",
        "data": {
            "total": 1,
            "skills": [
                {
                    "slug": "demo-skill",
                    "name": "Demo Skill",
                    "description": "Default description",
                    "description_zh": "中文描述",
                    "version": "1.2.3",
                    "ownerName": "Demo Author",
                    "namespace": {
                        "handle": "community_demo",
                        "displayName": "Demo Author",
                    },
                    "category": "dev-programming",
                    "subCategories": [{"key": "testing", "name": "测试"}],
                    "tags": ["python", "testing"],
                    "downloads": 42,
                    "installs": 7,
                    "stars": 3,
                    "verified": True,
                    "labels": {"requires_api_key": "true"},
                    "upstream_url": "https://github.com/example/demo",
                    "homepage": "https://skillhub.cn/skills/community_demo/demo-skill",
                }
            ],
        },
    }

    result = normalize_skillhub_response(payload, page=2, page_size=10)

    assert result["schemaVersion"] == 1
    assert result["provider"] == "skillhub"
    assert result["pagination"] == {"page": 2, "pageSize": 10, "total": 1}
    skill = result["skills"][0]
    assert skill["canonicalId"] == "skillhub:@community_demo/demo-skill"
    assert skill["coordinate"] == {"namespace": "community_demo", "slug": "demo-skill"}
    assert skill["display"]["description"] == "中文描述"
    assert skill["publisher"]["name"] == "Demo Author"
    assert skill["metrics"] == {"downloads": 42, "installs": 7, "stars": 3}
    assert skill["trust"] == {"verified": True, "level": "verified"}
    assert skill["requirements"] == {"requiresApiKey": True}
    assert skill["install"] == {
        "strategy": "registry-zip",
        "locator": "skillhub:@community_demo/demo-skill",
        "version": "1.2.3",
    }
    assert "slug" not in skill
    assert "upstream_url" not in skill


def test_invalid_skillhub_payload_is_not_exposed_to_callers() -> None:
    with pytest.raises(ValueError, match="upstream unavailable"):
        normalize_skillhub_response({"code": 503, "message": "upstream unavailable"})


def test_marketplace_install_descriptor_resolves_to_versioned_locator() -> None:
    source = resolve_marketplace_install_source(
        {
            "strategy": "registry-zip",
            "locator": "skillhub:@community_demo/demo-skill",
            "version": "1.2.3",
        }
    )

    assert source == "skillhub:@community_demo/demo-skill?version=1.2.3"
    locator = parse_skillhub_locator(source)
    assert build_skillhub_download_url(locator) == (
        "https://api.skillhub.cn/api/v1/download"
        "?slug=demo-skill&namespace=community_demo&version=1.2.3"
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "https://skillhub.cn/skills/community_demo/demo-skill",
            "skillhub:@community_demo/demo-skill",
        ),
        ("https://skillhub.cn/skills/demo-skill", "skillhub:demo-skill"),
        (
            "https://api.skillhub.cn/community_demo/demo-skill?v=1.2.3",
            "skillhub:@community_demo/demo-skill?version=1.2.3",
        ),
        (
            "https://api.skillhub.cn/api/v1/download"
            "?slug=demo-skill&namespace=community_demo&version=1.2.3",
            "skillhub:@community_demo/demo-skill?version=1.2.3",
        ),
    ],
)
def test_skillhub_public_urls_normalize_to_canonical_locator(source: str, expected: str) -> None:
    assert normalize_skillhub_source(source) == expected


def test_non_skillhub_url_is_left_for_other_install_providers() -> None:
    assert normalize_skillhub_source("https://github.com/example/demo") is None


@pytest.mark.parametrize(
    "value",
    [
        "skillhub:@missing-slug",
        "skillhub:namespace/slug",
        "skillhub:@namespace/../slug",
        "skillhub:bad slug",
    ],
)
def test_skillhub_locator_rejects_ambiguous_or_unsafe_coordinates(value: str) -> None:
    with pytest.raises(ValueError):
        parse_skillhub_locator(value)
