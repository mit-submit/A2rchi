# Developers Guide

Below is all the information which developers may need in order to get started contributing to the A2rchi project.

## Editing Documentation.

Editing documentation requires you to install the mkdocs python packge:
```
pip install mkdocs
```
To edit documentation, simply make changes to the `.md` and `.yml` files in the `./docs` folder. To view your changes without pushing them, `cd` into the `./docs` folder and then run `mkdocs serve`. Add the `-a IP:HOST` argument (default is localhost:8000) to specify where to host the docs so you can easily view your changes on the web.

To publish your changes, run `mkdocs gh-deploy`. Please make sure to also open a PR to merge the documentation changes into main.

Note, please do NOT edit files in the gh-pages branch by hand, again, make a PR to main from a separate branch, and then you can deploy from main with the new changes.
