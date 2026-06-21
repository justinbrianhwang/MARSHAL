"""
Weather presets for cinematic MARSHAL benchmark.
Designed for VLM readability and GTA V style aesthetic.
"""

# GOLDEN_HOUR: warm low sun ~28 deg altitude, light haze, partly cloudy. Cinematic long shadows but TL is still readable.
GOLDEN_HOUR_KW = {
    "cloudiness": 15.0,
    "precipitation": 0.0,
    "precipitation_deposits": 0.0,
    "wind_intensity": 10.0,
    "sun_azimuth_angle": -1.0,
    "sun_altitude_angle": 28.0,
    "fog_density": 2.0,
    "fog_distance": 0.75,
    "fog_falloff": 0.1,
    "wetness": 0.0,
    "scattering_intensity": 1.0,
    "mie_scattering_scale": 0.03,
    "rayleigh_scattering_scale": 0.0331,
    "dust_storm": 0.0
}

# BLUE_HOUR: just after sunset, sun ~6 deg, cool light, deeper haze for atmosphere.
BLUE_HOUR_KW = {
    "cloudiness": 10.0,
    "precipitation": 0.0,
    "precipitation_deposits": 0.0,
    "wind_intensity": 5.0,
    "sun_azimuth_angle": -1.0,
    "sun_altitude_angle": -6.0,
    "fog_density": 5.0,
    "fog_distance": 0.5,
    "fog_falloff": 0.2,
    "wetness": 0.0,
    "scattering_intensity": 1.0,
    "mie_scattering_scale": 0.03,
    "rayleigh_scattering_scale": 0.0331,
    "dust_storm": 0.0
}

# OVERCAST_DRAMA: heavy cloud, sun ~50 deg but cloud-veiled, flat moody light, slight haze.
OVERCAST_DRAMA_KW = {
    "cloudiness": 85.0,
    "precipitation": 0.0,
    "precipitation_deposits": 0.0,
    "wind_intensity": 20.0,
    "sun_azimuth_angle": -1.0,
    "sun_altitude_angle": 50.0,
    "fog_density": 8.0,
    "fog_distance": 1.0,
    "fog_falloff": 0.1,
    "wetness": 0.0,
    "scattering_intensity": 1.0,
    "mie_scattering_scale": 0.03,
    "rayleigh_scattering_scale": 0.0331,
    "dust_storm": 0.0
}

# AFTERNOON_HAZY: bright daytime ~50 deg, mild haze for depth — safe default.
AFTERNOON_HAZY_KW = {
    "cloudiness": 20.0,
    "precipitation": 0.0,
    "precipitation_deposits": 0.0,
    "wind_intensity": 10.0,
    "sun_azimuth_angle": -1.0,
    "sun_altitude_angle": 50.0,
    "fog_density": 4.0,
    "fog_distance": 1.5,
    "fog_falloff": 0.1,
    "wetness": 0.0,
    "scattering_intensity": 1.0,
    "mie_scattering_scale": 0.03,
    "rayleigh_scattering_scale": 0.0331,
    "dust_storm": 0.0
}

def apply(world, preset_name: str, carla_module) -> dict:
    """
    Apply a weather preset to the CARLA world.
    
    Args:
        world: The carla.World instance.
        preset_name: Key in PRESETS (e.g., 'GOLDEN_HOUR').
        carla_module: The carla python module (to access WeatherParameters).
        
    Returns:
        The applied dictionary of kwargs.
    """
    presets = {
        "GOLDEN_HOUR": GOLDEN_HOUR_KW,
        "BLUE_HOUR": BLUE_HOUR_KW,
        "OVERCAST_DRAMA": OVERCAST_DRAMA_KW,
        "AFTERNOON_HAZY": AFTERNOON_HAZY_KW
    }
    
    if preset_name not in presets:
        raise ValueError(f"Unknown preset: {preset_name}. Available: {list(presets.keys())}")
        
    kw = presets[preset_name]
    weather = carla_module.WeatherParameters(**kw)
    world.set_weather(weather)
    return kw
