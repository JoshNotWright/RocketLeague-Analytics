import os

def clear_files(clear_teams, clear_replays):
    if clear_teams:
        file_names = ["player_war.txt", "player_defensive_stats.txt", "player_offensive_stats.txt", "player_defensive_war.txt", "player_offensive_war.txt", "team_stats.txt", "unique_teams.txt"]
    else:
        file_names = ["player_war.txt", "player_defensive_stats.txt", "player_offensive_stats.txt", "player_defensive_war.txt", "player_offensive_war.txt", "team_stats.txt"]
    
    for file_name in file_names:
        with open(file_name, "w") as f:
            f.truncate(0)
    
    print("Files cleared successfully.")

    if clear_replays:
        # Remove the "replays" directory
        if os.path.exists("replays"):
            os.system("rmdir /s /q replays")
            print("Replays cleared successfully")

clear_files(True, True)