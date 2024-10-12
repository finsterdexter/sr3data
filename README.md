# SR3data

The goal of this project is to open up and make generally available the data from the venerable [NSRCG](https://nsrcg.neocities.org/) character generator for Shadowrun 3rd Edition. The data for that program was all bespoke data formats, mostly pipe delimited, but also... not? This is just a bunch of python code to parse the files and output a bunch of json. Hopefully, the json can then be used as the basis for VTT modules that want to use this SR3 data.

The main script is `export_json.py` so just run that. I'll include a poetry project file if you want to use that, I guess. Otherwise, just `python export_json.py` and it will dump the json into an `output` directory. I am committing the current `output` in case people just want the resultant data without having to worry about the python script. Currently, the dependencies are pretty simple (`re` and `json`).

In terms of licensing, I tried to track down the last maintainer of NSRCG, and looks like the hotmail address (mcmackie@hotmail.com) is just no longer monitored or something.
