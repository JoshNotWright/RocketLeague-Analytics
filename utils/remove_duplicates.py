def remove_duplicates_war(filename):
    with open(filename, 'r') as f:
        lines = f.readlines()
        
    players = {}
    for line in lines:
        data = line.strip().split(': ')
        name = data[0]
        war, games_played = map(float, data[1].split(', '))
        if name not in players:
            players[name] = (float(war), float(games_played))
        else:
            if games_played > players[name][1]:
                players[name] = (float(war), float(games_played))
    
    with open(filename, 'w') as f:
        for name, stats in players.items():
            f.write(f"{name}: {stats[0]}, {stats[1]}\n")

def remove_duplicates_stats(filename):
    with open(filename, 'r') as f:
        lines = f.readlines()

    players = {}
    for line in lines:
        data = line.strip().split(';')
        name = data[0]
        stats = tuple(map(float, data[1:]))
        if name not in players:
            players[name] = stats
        else:
            if stats[-1] > players[name][-1]:
                players[name] = stats

    with open(filename, 'w') as f:
        for name, stats in players.items():
            f.write(f"{name};{';'.join(str(s) for s in stats)}\n")