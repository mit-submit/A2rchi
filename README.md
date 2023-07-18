# A2rchi
An AI Augmented Research Chat Intelligence for MIT's subMIT project in the physics department

## Setup

### OpenAI key

A2rchi uses OpenAI Large Language Models (LLM's) to help answer questions. To do this, you need to have an OpenAI API key (starts with "sk-"). It is [important](https://help.openai.com/en/articles/5112595-best-practices-for-api-key-safety)  that API keys are never added directly to the code. Instead, go to your .bashrc file (for example by using `vim ~/.bashrc`) and adding the line `export OPENAI_API_KEY=sk-8888` and then pasting your API key instead of sk-8888. 

### Conda Environment

The environment.yml file contains the needed requirements to run A2rchi. To create the A2rchi environment, simply run

```
conda env create -f environment.yml -n "A2rchi_env"
```

in the repository (this may take awhile). Then activate it using

```
conda activate A2rchi_env
```

(You need not create the environment everytime you log in, butyou do need to activate it)

### Scraping the data

For safety and security reasons, data should never be directly added to the git repo. Instead, we use a small script `scraper.py` to scrape the information which is used by A2rchi off the internet and place it in the `data/` directory. To run the scraper, simply run

```
python scraper.py
```

in the conda environment. Additionially, you may want to remove the placeholder files by running

```
rm data/github/info.txt
rm data/submit-website/info.txt
```

You may get some warnings while running the scraper. This is normal for now (will hopefully get cleaned up soon). You only need to run the scraper when there is new data to be added.

### Running the app

To run the app where the chatbot appears, simply run 

```
python app.py
```

in your conda environment. You will see a public temporary link pop up which you can click on to display an app for the chatbot. Once you kill the process (for example with `ctrl-c`), the app will no longer work.

### Deploying the app

<<<<<<< HEAD
Coming soon!
=======
Coming soon!
>>>>>>> 01cd057ac6c1daf889b86ab9861cff7afaeab070
