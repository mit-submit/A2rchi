# Developers Guide

Below is all the information which developers may need in order to get started contributing to the A2rchi project.

## Editing Documentation.

Editing documentation requires you to install the mkdocs python packge:
```
pip install mkdocs
```
To edit documentation, simply make changes to the `.md` and `.yml` files in the `./docs` folder. To view your changes without pushing them, `cd` into the `./docs` folder and then run `mkdocs serve`. To publish your changes, run `mkdocs gh-deploy`. Please make sure to also open a PR to merge the documentation changes into main.