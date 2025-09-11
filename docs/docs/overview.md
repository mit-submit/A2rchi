# A2rchi

A2rchi (AI Augmented Research Chat Intelligence) is a retrieval-augmented generation (RAG) framework designed to be a low-barrier, open source, private, and customizable AI solution for research and educational support.

A2rchi makes it easy to deploy AI assistants with a suite of tools to connect to various platforms (e.g., Piazza, Slack, Discourse, Mattermost, email) and knowledge bases (e.g., links, files, JIRA tickets, documentation). It is designed to be modular and extensible, allowing users to easily add new connectors and customize the behavior of the AI assistants.

A2rchi also allows the design of custom pipelines to process user queries, including tools for retrieval, generation, and post-processing. This makes it easy to create AI assistants that can handle a wide range of tasks, from answering simple questions to providing detailed explanations and support.

## About

A2rchi is an open source, end-to-end framework to quickly and easily build an AI support agent for classes and research resources developed jointly by Prof. Paus, MIT Physics and Prof. Kraska, MIT EECS, and their students. It has already been successfully deployed as an user chatbot and sysadmin assistant at SubMIT, the private cluster at MIT’s Physics Department, and as an educational assistant for several courses at MIT, including the 8.01 and 8.511, among others. Read below some example deployments and use cases.

A2rchi is not the first or only AI support agent framework, but what we believe sets it apart is that it is fully open-source and customizable, works with different foundational models and LLM libraries,  makes it easy to design custom AI-pipelines for your needs, and comes with a suite of services to interact with various popular platforms like Piazza, Redmine, Slack, JIRA, Discourse, and Mattermost, among others. As such, it can be entirely locally deployed and restricted to locally hosted open-source foundational models; a requirement whenever potential sensitive (student, user) data is involved. Under the hood, A2rchi is a highly configurable Retrieval Augmented Generation (RAG) system specifically designed for education and support for science resources. Given the success of A2rchi so far we are now expanding its scope to more MIT classes, CERN, Harvard, and other internal use cases (e.g., support for CSAIL’s support staff).

### Educational Support

A2rchi can be used as a tool for TAs, lecturers, and support staff to prepare answers or directly by students to receive help without human involvement.
For example, in the A2rchi deployments to MIT courses, A2rchi provides high-quality answers by using previous Piazza posts, student questions, documentation, and other class/system-specific documents.
Using the Piazza plug-in, A2rchi can help to prepare an answer based on a student's question. The TA/lecturer can then approve or modify the answer before sending it out, or A2rchi can send out the reply directly.
Moreover, A2rchi continuously learns from any potential modification or from other Piazza posts, which are answers by TAs. So the answers keep improving over time, and are highly class-specific.

### Research Resource Support

A2rchi can be used by technical support staff to prepare answers or directly by users to receive help without human involvement.
For example, in the deployment to SubMIT, the MIT Physics Department's computing cluster, A2rchi is used both as an user-facing chatbot to answer questions and provide expert assistance, and as an assistant to the technical support staff by integrating with the Redmine ticketing system, where A2rchi prepares answers to the user's tickets, which can then be reviewed by the support staff before being answered.
In both cases, A2rchi has access to the corpus of all tickets and documentation from the SubMIT cluster to learn from, and can provide specific sources as part of its answer. 
