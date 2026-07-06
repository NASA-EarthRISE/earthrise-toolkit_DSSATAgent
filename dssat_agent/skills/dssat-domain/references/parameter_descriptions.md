# DSSAT Experiment Parameters

## county
An Alabama county name. There are 67 valid counties:
Autauga, Baldwin, Barbour, Bibb, Blount, Bullock, Butler, Calhoun, Chambers, Cherokee,
Chilton, Choctaw, Clarke, Clay, Cleburne, Coffee, Colbert, Conecuh, Coosa, Covington,
Crenshaw, Cullman, Dale, Dallas, DeKalb, Elmore, Escambia, Etowah, Fayette, Franklin,
Geneva, Greene, Hale, Henry, Houston, Jackson, Jefferson, Lamar, Lauderdale, Lawrence,
Lee, Limestone, Lowndes, Macon, Madison, Marengo, Marion, Marshall, Mobile, Monroe,
Montgomery, Morgan, Perry, Pickens, Pike, Randolph, Russell, Shelby, St. Clair, Sumter,
Talladega, Tallapoosa, Tuscaloosa, Walker, Washington, Wilcox, Winston

## planting_date
The date the crop is planted, in YYYY-MM-DD format.

## fertilization_plan
A list of nitrogen applications, each specified as [nitrogen_kg_per_ha, days_after_planting].
Example: [[100, 0], [50, 30]] means 100 kg/ha at planting and 50 kg/ha at 30 days after planting.

## season_length
The cultivar maturity class. One of: very short, short, medium, long, very long.
Maps to DSSAT cultivar codes via cultivar_mapping.json.
