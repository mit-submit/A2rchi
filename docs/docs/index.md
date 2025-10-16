# A2RCHI

A2RCHI (AI Augmented Research Chat Intelligence) is a retrieval-augmented generation (RAG) framework designed to be a low-barrier, open-source, private, and customizable AI solution for research and educational support.

A2RCHI makes it easy to deploy AI assistants with a suite of tools that connect to

- communication platforms such as Piazza, Slack, Discourse, Mattermost, and email, and
- knowledge bases such as web links, files, JIRA tickets, and documentation.

It is modular and extensible, allowing users to add connectors and customise pipeline behaviour for a wide range of tasks—from answering simple questions to delivering detailed explanations.

## About

A2RCHI is an end-to-end framework developed by Prof. Paus (MIT Physics), Prof. Kraska (MIT EECS), and their students. It has already been successfully deployed as a user chatbot and technical assistant at SubMIT (the MIT Physics Department's private cluster) and as an educational assistant for several MIT courses, including 8.01 and 8.511.

What sets A2RCHI apart is that it is fully open source, configurable across foundational models and LLM libraries, and designed for private deployment. Under the hood, A2RCHI is a highly configurable RAG system tailored for educational and scientific support. Given its success, the scope now spans additional MIT classes, CERN, Harvard, and internal deployments such as CSAIL's support staff.

### Educational Support

A2RCHI assists TAs, lecturers, and support staff—or students directly—by preparing answers based on curated class resources. In MIT course deployments, A2RCHI leverages Piazza posts, documentation, and other class-specific materials. The Piazza integration can draft answers for staff to review or send, while the system continuously learns from revisions and new posts, improving over time.

### Research Resource Support

A2RCHI also serves technical support teams and end users. At SubMIT, it functions both as a user-facing chatbot and as a ticket assistant. Integration with Redmine enables A2RCHI to prepare draft responses to support tickets that staff can review before sending. In both roles, A2RCHI accesses the corpus of tickets and documentation, citing relevant sources in its answers.
