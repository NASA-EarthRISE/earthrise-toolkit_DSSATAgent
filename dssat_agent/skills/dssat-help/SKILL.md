---
name: dssat-help
description: DSSAT Agent help content for composing into chat help.
composable: true
compose_into: help
---

## Help

**Running a Simulation:**
- Describe what you want: "Simulate maize in Cullman County planted March 15, 2024"
- Minimum needed: crop type, location, planting date
- The agent auto-selects soil, cultivar, and weather source if not specified
- You can specify: soil ID, weather source, fertilizer plan, irrigation strategy

**Experiment Types:**
- **Single Simulation**: Point prediction for a specific scenario — "Simulate maize in Auburn planted May 1"
- **Ensemble (Comparison)**: Compare management strategies side by side — "Compare 3 fertilizer plans for my corn", "What's the best planting date?"
- **Sensitivity Analysis**: Test how one parameter affects yield — "How sensitive is yield to nitrogen rate?"
- **Monte Carlo (Spatial)**: Regional yield distribution with confidence intervals — "What is the expected yield of corn in Alabama?", "How does yield vary across the county?"

**Data Explorer Pages:**
- /dssat/soils/ — Browse, create, edit soil profiles
- /dssat/crops/ — Browse crops and cultivars
- /dssat/experiments/ — View past simulation results
- /dssat/codes/ — DSSAT code reference
- /dssat/experiment/ — Step-by-step experiment wizard

**Soil Profiles:**
- Build custom soils at /dssat/soils/create/
- Estimate from texture at /dssat/soils/create-from-texture/
- Or specify in chat: "use soil IB00000001"
