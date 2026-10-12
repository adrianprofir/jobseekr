(function () {
  "use strict";

  var CONSENT_COOKIE = "ap_analytics_consent";
  var CONSENT_MAX_AGE = 15552000;

  function readConsent() {
    var parts = document.cookie ? document.cookie.split("; ") : [];
    for (var i = 0; i < parts.length; i++) {
      var pair = parts[i].split("=");
      if (pair[0] === CONSENT_COOKIE) {
        return decodeURIComponent(pair.slice(1).join("="));
      }
    }
    return null;
  }

  function writeConsent(value, config) {
    var cookie =
      CONSENT_COOKIE +
      "=" +
      encodeURIComponent(value) +
      "; Path=/; Max-Age=" +
      CONSENT_MAX_AGE +
      "; SameSite=Lax";
    if (config.secure_cookies) {
      cookie += "; Secure";
    }
    if (config.cookie_domain) {
      cookie += "; Domain=" + config.cookie_domain;
    }
    document.cookie = cookie;
  }

  function loadPostHogStub() {
    if (window.posthog && window.posthog.__SV) {
      return;
    }
    /* PostHog official snippet loader (loads array.js from the EU assets host). */
    !(function (t, e) {
      var o, n, p, r;
      e.__SV ||
        ((window.posthog = e),
        (e._i = []),
        (e.init = function (i, s, a) {
          function g(t, e) {
            var o = e.split(".");
            2 == o.length && ((t = t[o[0]]), (e = o[1])),
              (t[e] = function () {
                t.push([e].concat(Array.prototype.slice.call(arguments, 0)));
              });
          }
          ((p = t.createElement("script")).type = "text/javascript"),
            (p.crossOrigin = "anonymous"),
            (p.async = !0),
            (p.src = s.api_host.replace(".i.", "-assets.i.") + "/static/array.js"),
            (r = t.getElementsByTagName("script")[0]).parentNode.insertBefore(p, r);
          var u = e;
          for (void 0 !== a ? (u = e[a] = []) : (a = "posthog"), u.people = u.people || [], u.toString = function (
            t
          ) {
            var e = "posthog";
            return "posthog" !== a && (e += "." + a), t || (e += " (stub loader)"), e;
          }, u.people.toString = function () {
            return u.toString(1) + ".people (stub)";
          }, o =
            "capture identify alias people.set people.set_once set_config register register_once unregister opt_out_capturing has_opted_out_capturing opt_in_capturing reset isFeatureEnabled onFeatureFlags getFeatureFlag getFeatureFlagPayload reloadFeatureFlags group updateEarlyAccessFeatureEnrollment getEarlyAccessFeatures getActiveMatchingSurveys getSurveys getNextSurveyStep onSessionId setPersonProperties".split(
              " "
            ), n = 0; n < o.length; n++)
            g(u, o[n]);
          e._i.push([i, s, a]);
        }),
        (e.__SV = 1));
    })(document, window.posthog || []);
  }

  var posthogInited = false;

  function initPostHog(config) {
    if (posthogInited) {
      return;
    }
    loadPostHogStub();
    window.posthog.init(config.project_key, {
      api_host: config.api_host,
      ui_host: config.ui_host,
      person_profiles: "always",
      cross_subdomain_cookie: true,
      persistence: "localStorage+cookie",
      session_recording: { maskAllInputs: true },
    });
    window.posthog.register({ app: config.app_name });
    posthogInited = true;
  }

  function grantAnalytics(config) {
    initPostHog(config);
    if (window.posthog && window.posthog.opt_in_capturing) {
      window.posthog.opt_in_capturing();
    }
  }

  function setBannerVisible(visible) {
    var banner = document.getElementById("analytics-banner");
    if (!banner) {
      return;
    }
    banner.classList.toggle("hidden", !visible);
    document.body.classList.toggle("analytics-banner-visible", visible);
  }

  function onReady(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn);
    } else {
      fn();
    }
  }

  onReady(function () {
    var configEl = document.getElementById("analytics-config");
    if (!configEl) {
      return;
    }
    var config = JSON.parse(configEl.textContent);
    var banner = document.getElementById("analytics-banner");
    var prefs = document.getElementById("analytics-prefs");
    if (!banner) {
      return;
    }

    var consent = readConsent();
    if (consent === "granted") {
      grantAnalytics(config);
    } else if (consent !== "denied") {
      setBannerVisible(true);
    }

    if (prefs) {
      prefs.addEventListener("click", function () {
        setBannerVisible(true);
      });
    }

    banner.querySelectorAll("[data-analytics-action]").forEach(function (button) {
      button.addEventListener("click", function () {
        var action = button.getAttribute("data-analytics-action");
        var previous = readConsent();
        if (action === "accept") {
          writeConsent("granted", config);
          setBannerVisible(false);
          grantAnalytics(config);
        } else if (action === "decline") {
          writeConsent("denied", config);
          setBannerVisible(false);
          if (previous === "granted" && window.posthog && window.posthog.opt_out_capturing) {
            window.posthog.opt_out_capturing();
          }
        }
      });
    });
  });
})();
