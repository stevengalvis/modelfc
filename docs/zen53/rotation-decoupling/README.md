# ZEN-53 rotation source distribution

This local distribution is for source review only. It contains two separate packages:

- The stage package is unchanged: `zen53-api-rotation-decoupling-stage-offline.tar.gz`, 126,618 bytes, SHA256 `a527085d5fb968fed1577a9afdea4d8173fef1b931ec06012cc49c3d909a4d33`, with package inventory SHA256 `c74f259e3a94fc31c1792d66c13fff4f2149430688dc18db932137db157a985c`.
- The reduced operator source distribution is `zen53-api-rotation-operator-v4-source-reduced-offline.tar.gz`, 67,338 bytes, SHA256 `692386f512bdea0dab54426c5a0336f4f9d0ae9fbcfce712eb89143275b705a1`. Its static operator and launcher-test trees retain every source byte, file metadata and internal inventory from the reviewed candidate. Its operator inventory SHA256 is `b8edd449ae8ef62b8596ba4c52c38c0f84dc7a82fd80636be9d529cc922ebd80`.

The operator distribution has a new outer archive hash and is not interchangeable with the preceding outer archive pin. The exact source-tree members and hashes are recorded in `ZEN53_API_ROTATION_REDUCED_SOURCE_MANIFEST.json`. A repository checkout is also not a substitute for either pinned archive in the existing VPS preparation contract. Any acquisition and installation procedure must authenticate the selected distribution independently before use.

No code here is installed or activated by this distribution. It does not create an approval record, change a service or security policy, launch the Python diagnostic, or run Codex.
