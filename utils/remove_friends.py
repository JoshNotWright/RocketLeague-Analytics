def remove_friends():
    
    # List of names to remove
    names_to_remove = [
        "PastorKurt",
        "PastorCamerius",
        "PastorRipper",
        # Add more names here if needed
    ]

    # File paths
    file_paths = [
        "player_offensive_war.txt",
        "player_defensive_war.txt",
        "player_offensive_stats.txt",
        "player_defensive_stats.txt",
        "player_war.txt",
    ]

    # Iterate over each file
    for file_path in file_paths:
        with open(file_path, "r") as file:
            # Read the content of the file
            lines = file.readlines()

        # Remove lines with specified names
        filtered_lines = [line for line in lines if not any(name in line for name in names_to_remove)]

        with open(file_path, "w") as file:
            # Write the filtered content back to the file
            file.writelines(filtered_lines)
