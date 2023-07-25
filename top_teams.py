def get_top_offensive_teams(file_names):
    for file_name in file_names:
        with open(file_name, "r") as f:
            lines = f.readlines()
            teams = []
            for line in lines:
                parts = line.strip().split(":")
                team_name = parts[0].strip()
                offensive_war = float(parts[1].split(";")[0])  # Extract offensive_war score
                teams.append((team_name, offensive_war))
            
            sorted_teams = sorted(teams, key=lambda x: x[1], reverse=True)
        
        print(f"Top 5 Offensive Teams in {file_name}:")
        for i, team in enumerate(sorted_teams[:5]):
            print(f"{i+1}. {team[0]} (Offensive War: {team[1]})")
        print()

def get_top_defensive_teams(file_names):
    for file_name in file_names:
        with open(file_name, "r") as f:
            lines = f.readlines()
            teams = []
            for line in lines:
                parts = line.strip().split(":")
                team_name = parts[0].strip()
                defensive_war = float(parts[1].split(";")[1])  # Extract defensive_war score
                teams.append((team_name, defensive_war))
            
            sorted_teams = sorted(teams, key=lambda x: x[1], reverse=True)
        
        print(f"Top 5 Defensive Teams in {file_name}:")
        for i, team in enumerate(sorted_teams[:5]):
            print(f"{i+1}. {team[0]} (Defensive War: {team[1]})")
        print()

def get_top_teams(file_names):
    for file_name in file_names:
        with open(file_name, "r") as f:
            lines = f.readlines()
            teams = []
            for line in lines:
                parts = line.strip().split(":")
                team_name = parts[0].strip()
                defensive_war = float(parts[1].split(";")[2])  # Extract war score
                teams.append((team_name, defensive_war))
            
            sorted_teams = sorted(teams, key=lambda x: x[1], reverse=True)
        
        print(f"Top 5 Teams in {file_name}:")
        for i, team in enumerate(sorted_teams[:5]):
            print(f"{i+1}. {team[0]} (War: {team[1]})")
        print()

def get_worst_offensive_teams(file_names):
    for file_name in file_names:
        with open(file_name, "r") as f:
            lines = f.readlines()
            teams = []
            for line in lines:
                parts = line.strip().split(":")
                team_name = parts[0].strip()
                offensive_war = float(parts[1].split(";")[0])  # Extract offensive_war score
                teams.append((team_name, offensive_war))
            
            sorted_teams = sorted(teams, key=lambda x: x[1])
        
        print(f"Worst 5 Offensive Teams in {file_name}:")
        for i, team in enumerate(sorted_teams[:5]):
            print(f"{i+1}. {team[0]} (Offensive War: {team[1]})")
        print()


def get_worst_defensive_teams(file_names):
    for file_name in file_names:
        with open(file_name, "r") as f:
            lines = f.readlines()
            teams = []
            for line in lines:
                parts = line.strip().split(":")
                team_name = parts[0].strip()
                defensive_war = float(parts[1].split(";")[1])  # Extract defensive_war score
                teams.append((team_name, defensive_war))
            
            sorted_teams = sorted(teams, key=lambda x: x[1])
        
        print(f"Worst 5 Defensive Teams in {file_name}:")
        for i, team in enumerate(sorted_teams[:5]):
            print(f"{i+1}. {team[0]} (Defensive War: {team[1]})")
        print()

def get_worst_teams(file_names):
    for file_name in file_names:
        with open(file_name, "r") as f:
            lines = f.readlines()
            teams = []
            for line in lines:
                parts = line.strip().split(":")
                team_name = parts[0].strip()
                defensive_war = float(parts[1].split(";")[2])  # Extract war score
                teams.append((team_name, defensive_war))
            
            sorted_teams = sorted(teams, key=lambda x: x[1])
        
        print(f"Worst 5 Teams in {file_name}:")
        for i, team in enumerate(sorted_teams[:5]):
            print(f"{i+1}. {team[0]} (War: {team[1]})")
        print()

file_names = ["team_stats.txt"]
get_top_offensive_teams(file_names)
get_top_defensive_teams(file_names)
get_top_teams(file_names)
get_worst_offensive_teams(file_names)
get_worst_defensive_teams(file_names)
get_worst_teams(file_names)

