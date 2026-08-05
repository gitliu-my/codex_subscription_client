# Design QA

## Comparison Target

- Source visual truth: user-provided management-console reference screenshot
- Implementation: `http://127.0.0.1:8422/#service` and `http://127.0.0.1:8422/#console`
- Implementation screenshots: local desktop and mobile QA captures (not committed)
- Viewport: desktop `1440 x 1024` CSS px; mobile `390 x 844` CSS px
- Density: device scale factor `1`
- Source pixels: `1487 x 1058`; normalized to the `1440 x 1024` target because the aspect ratios match within 0.1%
- Implementation pixels: `1440 x 1024` desktop and `390 x 844` mobile
- State: ChatGPT logged in, local API stopped, console waiting for a request; Key list populated

## Full-View Comparison

The desktop debugger capture follows the selected Quiet Control Room composition: a fixed 238 px dark navigation rail, service and account cards anchored at the bottom, a full-width debugger header above the work area, a dominant prompt/result surface, and a 440 px request inspector. The implementation intentionally shows the real empty result state instead of fabricating a completed response.

## Focused Evidence

- The request inspector starts below the shared debugger header and keeps the request model, effort,
  and metrics aligned to the reference.
- API Console is now a separate service-management view. It keeps default model, effort, port,
  concurrency, service state, and three computed endpoint displays together without mixing in test inputs.
- Endpoint values use read-only code blocks with copy actions. Editing the port updates all three
  displayed addresses immediately, while service and debugger model selections remain independent.
- API Keys switches to a full-width management table while preserving the same fixed navigation and account/service controls.
- The mobile capture keeps navigation, service controls, login controls, mode switches, and prompt inputs available without horizontal overflow.
- Persistent API and ChatGPT controls now use one state-aware button each. At `390 x 844` they
  share a single 52 px row rather than stacking two large cards, with no text or control overflow.
- The authenticated display name opens a local account detail popover with email, raw plan type,
  and Account ID. The desktop and mobile captures keep it within the viewport without overflow.
- API Key empty-name validation returns focus to the name field and auto-dismisses after 3.6 seconds.
- The API Debugger header remains at `y = 0` while the document scrolls, keeping mode controls visible.
- Focused crops were unnecessary because the native desktop captures render labels, fields, and status cards legibly.

## Required Fidelity Surfaces

- Fonts and typography: system UI and monospace fallbacks preserve the compact developer-console hierarchy, with zero letter spacing and readable field labels.
- Spacing and layout rhythm: sidebar width, header height, main/inspector split, prompt heights, upload row, run bar, result tabs, 4-6 px radii, and divider rhythm closely follow the source.
- Colors and visual tokens: dark navy navigation, white working canvas, blue primary actions, emerald health state, and coral destructive actions match the selected direction.
- Image quality and assets: the source has no required product imagery. Browser-uploaded and model-generated images still use the production rendering paths; no placeholder art was introduced.
- Copy and content: OAuth, API service, runtime configuration, multimodal controls, streaming, metrics, output tabs, and Key permissions all describe real implemented behavior.

## Comparison History

1. The first implementation used the wrong generated concept and was rejected by the user.
2. Rebuilt against the exact user-supplied screenshot, moving navigation, service state, and login into the fixed left rail and request metrics into the right inspector.
3. Initial corrected capture found a P2 header mismatch: mode controls wrapped because the header was clipped to the main grid column. Extended the header across both workspace columns and offset the inspector content below it.
4. Initial mobile capture hid the ChatGPT card. Restored it so login remains reachable on small screens and recaptured at `390 x 844`.
5. Post-fix desktop and mobile evidence show no overlap, horizontal overflow, clipped persistent controls, or missing core actions.
6. Split the former combined console into API Console, API Debugger, and API Keys. Verified the new
   service view and debugger against the same desktop composition and at the `390 x 844` mobile viewport.
7. Replaced the two verbose sidebar cards with compact API and ChatGPT status controls. Compared
   before/after mobile captures and verified both controls fit side by side at 390 px.
8. Added the authenticated account popover, auto-dismissing validation feedback, and a sticky
   debugger header. Verified click/outside-close behavior, focus return, timeout dismissal, and
   desktop/mobile placement against live account data.

## Findings

No actionable P0, P1, or P2 differences remain.

The reference uses decorative navigation and upload icons. They remain a P3 visual difference because the self-contained dashboard does not currently ship an icon library; no text glyphs or handcrafted SVG substitutes were introduced.

## Primary Interactions Tested

- API Console, API Debugger, and API Keys navigation
- State-aware API start/stop and ChatGPT login/logout controls
- Authenticated account detail popover on hover/focus/click
- Empty API Key name validation focus and dismissal timing
- Sticky API Debugger header during document scrolling
- Service port changes updating Base URL, Chat Completions, and Responses read-only displays
- Service model selection remaining independent from the debugger request model
- API Key permission dialog open and cancel
- Direct-subscription and local-API mode switching
- Chat Completions and Responses format switching
- Run-button labels updating with the selected mode
- Browser console error log checked: no errors

## Follow-up Polish

- P3: Vendor a small, audited icon-library subset if native navigation and upload icons become important enough to justify the extra packaged assets.

final result: passed
