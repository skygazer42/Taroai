from urllib.request import Request

import pytest

from taroai.skills.import_service import GithubFetchPolicy, _GithubRedirectHandler


@pytest.mark.parametrize(
    "target_url",
    [
        "http://github.com/archive.zip",
        "https://example.com/archive.zip",
        "https://github.com:444/archive.zip",
    ],
)
def test_github_redirect_is_validated_before_following(target_url: str):
    handler = _GithubRedirectHandler(GithubFetchPolicy())

    with pytest.raises(ValueError):
        handler.redirect_request(
            Request("https://codeload.github.com/owner/repository/zip/main"),
            None,
            302,
            "Found",
            {},
            target_url,
        )


def test_github_redirect_limit_is_enforced_before_following():
    handler = _GithubRedirectHandler(GithubFetchPolicy(max_redirects=1))
    request = Request("https://codeload.github.com/owner/repository/zip/main")
    redirected = handler.redirect_request(
        request,
        None,
        302,
        "Found",
        {},
        "https://github.com/owner/repository/archive/main.zip",
    )

    with pytest.raises(ValueError, match="redirect limit"):
        handler.redirect_request(
            redirected,
            None,
            302,
            "Found",
            {},
            "https://codeload.github.com/owner/repository/zip/main",
        )
