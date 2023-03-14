def get_war_min_max(file_path_offensive_war: str, file_path_defensive_war: str):
    offensive_war = []
    defensive_war = []

    # Read the offensive WAR and defensive WAR from their respective files
    with open(file_path_offensive_war, 'r') as f:
        for line in f.readlines():
            split_line = line.strip().split(': ')
            if len(split_line) < 2:
                continue
            war, games = split_line[1].split(', ')
            offensive_war.append(float(war))

    with open(file_path_defensive_war, 'r') as f:
        for line in f.readlines():
            split_line = line.strip().split(': ')
            if len(split_line) < 2:
                continue
            war, games = split_line[1].split(', ')
            defensive_war.append(float(war))

    # Apply min-max scaling to offensive and defensive WAR
    offensive_min = min(offensive_war)
    offensive_max = max(offensive_war)
    defensive_min = min(defensive_war)
    defensive_max = max(defensive_war)

    return offensive_min, offensive_max, defensive_min, defensive_max

def scale_war(war_value: float, war_type: str) -> float:
    offensive_min, offensive_max, defensive_min, defensive_max = get_war_min_max("player_offensive_war.txt", "player_defensive_war.txt")
    
    if war_type == 'offensive':
        scaled_value = (war_value - offensive_min) / (offensive_max - offensive_min)
    elif war_type == 'defensive':
        scaled_value = (war_value - defensive_min) / (defensive_max - defensive_min)
    else:
        raise ValueError("Invalid war type. Must be 'offensive' or 'defensive'")
        
    return scaled_value