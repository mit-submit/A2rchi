A2rchi (AI Augmented Research Chat Intelligence) is an open source, end-to-end framework to quickly
build an AI support agent for classes and research resources developed jointly by Prof. Paus, MIT
Physics and Prof. Kraska, MIT EECS and their students. It has already been successfully deployed for SubMIT, the
private cloud at MIT’s physic department, and for the 8.01 and 8.511 courses. Currently, A2rchi is
integrated with Redmine, a widely used open-source project management tool (including a ticketing
system), and Piazza, a popular discussion forum for courses that MIT uses; apart from information
contained in Piazza and Redmine, A2rchi consumes specialized information from documents that au-
thorized users can upload through a A2rchi’s web uploader. A2rchi can be used as a tool for TAs,
lecturers, and support staff to prepare answers or directly by students to receive help without human
involvement. To provide high-quality answers A2rchi uses previous Piazza posts, tickets, documenta-
tion, and other class/system-specific documents. For example, in Piazza A2rchi is a plug-in that can
help to prepare an answer based on a Piazza post. The TA/lecturer can then approve or modify the
answer before sending it out, or A2rchi can send out the reply directly. Moreover, A2rchi continuously
learns from any potential modification or from other Piazza posts, which are answers by TAs. So the
answers keep improving over time, and are highly class-specific. The principle for research resource
support, e.g., SubMIT, is similar except that users send email to the help desk which are automatically
answered and converted to support tickets in Redmine using A2rchi plugins. The technical support
staff then has to review the answers and trigger the reply to the user. In addition, A2rchi can also
be deployed to be directly used by students as a chatbot to seek help at any time and without hu-
man involvement. Its chat agent uses the same knowledge base as the plugin for TAs/lecturers/staff
members.

A2rchi is not the first GenAI support agent, but what sets A2rchi apart is that it is fully open-source, works with different
foundational models, and most importantly is interacting with various platforms like Piazza, Red-
mine, Slack, Discourse, and Mattermost. As such, it can be entirely locally deployed and
restricted to locally hosted open-source foundational models; a requirement whenever potential sensi-
tive (student, user) data is involved. Under the hood, A2rchi is a highly configurable Retrieval Augmented
Generation (RAG) system specifically designed for education and support for science resources.
Given the success of A2rchi so far we are now expanding its scope to more MIT classes,
CERN, Harvard, and other internal use cases (e.g., support for CSAIL’s support staff).
