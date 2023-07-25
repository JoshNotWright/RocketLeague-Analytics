def get_best_players(file_names):
    for file_name in file_names:
        with open(file_name, "r") as f:
            lines = f.readlines()[1:]  # Skip the header line
            players = []
            for line in lines:
                parts = line.strip().rsplit(":", 1)
                name = parts[0].strip()
                score, num_games = parts[1].split(",")
                players.append((name, float(score)))
            
            sorted_players = sorted(players, key=lambda x: x[1], reverse=True)
        
        print(f"Top 5 Players in {file_name}:")
        for i, player in enumerate(sorted_players[:5]):
            print(f"{i+1}. {player[0]} (Score: {player[1]})")
        print()

def get_worst_players(file_names):
    for file_name in file_names:
        with open(file_name, "r") as f:
            lines = f.readlines()[1:]  # Skip the header line
            players = []
            for line in lines:
                parts = line.strip().rsplit(":", 1)
                name = parts[0].strip()
                score, num_games = parts[1].split(",")
                players.append((name, float(score)))
            
            sorted_players = sorted(players, key=lambda x: x[1])
        
        print(f"Worst 5 Players in {file_name}:")
        for i, player in enumerate(sorted_players[:5]):
            print(f"{i+1}. {player[0]} (Score: {player[1]})")
        print()


file_names = ["player_war.txt", "player_offensive_war.txt", "player_defensive_war.txt"]
get_best_players(file_names)
get_worst_players(file_names)
