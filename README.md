# Satisfactory Train Station Rearranger
A tool to rearrange the order of Train Stations in the Time Table menu by editing the save file.
Currently updated for at least Update 1.0, v1.0.0.3 (build 368883).

Usage instructions are down below.

## The problem
The Stations in the Time Table menu are ordered by the time they were built,
and when an older Station is destroyed, the Station at the bottom of the list (the newest) will jump up in the list to take the destroyed station's place.
When building a larger rail network, this can lead to some pretty nasty and unorganised lists.

Or perhaps you'd like some stations to be at the top of the list because they're used more often. Like an on-demand Train Mall system.

Satisfactory currently does not have a way of changing the order of Stations, which is why I wrote this tool!

![](./img/before_after.png)

## What this tool can do
- Change the order in which the Stations appear in the Time Table menu.

## What this tool can *not* do
- Edit the order of Trains in the Time Table menu. This was possible before, but since 1.0, Trains are sorted alphabetically, so this tool can't change them anymore.
- Edit the names of Stations.
- Remove or add Stations.
- Change the order in which Stations appear in the Map's menu.
- Do anything with non-train vehicles.

## Usage
1. Download the latest `train_rearranger.exe` from the [Release page](https://github.com/SimonvBez/SatisfactoryTrainRearranger/releases/).
(Some antiviruses might not like this. Create an exception, or consider to run from source instead of using the release .exe)
   - If you rather run from source instead of downloading a release, download and install [Python](https://www.python.org/downloads/) and download [train_rearrenger.py](./train_rearranger.py) from this repo.
2. In file explorer, drag and drop your savefile onto the `train_rearrenger.exe/py` file.
![](./img/drag_and_drop.png)
3. A text file will be created: `station list.txt`. Read the program's instructions and edit the file to your liking.
![](./img/reorder_instructions.png)
4. Don't forget to save your changes to `station list.txt`!
5. Press Enter in the program's window to read the new orders from the file. If the text file is correct (no names are missing, misspelled or duplicated) a new save file will be generated.
6. Put the new save file in Satisfactory's SaveGames folder and enjoy!

## FAQ
- What if I mess up the text file (like if I misspelled or missed a Station name)?
  - The program will check that your text file is correct and informs you if something is wrong.
A new save file will only be generated if the text file contains all the names.
- Does this work with mods?
  - I have only used this on a vanilla save, but my guess is probably! Make a backup of your original save just in case though.
- What if I have several Stations of the same name?
  - No worries! Though they can only stay in the same relative order. For example if you have 2 Stations both named "Iron Ore", the first "Iron Ore" in the old list will always be the first "Iron Ore" in the new list, second the second, etc.
- Does rearranging the Stations mess with any of the Self-Driving Trains?
  - No, all Trains will continue working as intended. This tool only changes the Time Table's visual list order.
- Does this tool work with several separated rail networks?
  - Yes, it'll work. If you have separate rail networks it will put all Stations into the same text file, but the game will still know that they are separate networks.
- Will you make a version with a proper user interface?
  - I have no plans for it, this currently works well enough for me. Feel free to fork and improve it though!
