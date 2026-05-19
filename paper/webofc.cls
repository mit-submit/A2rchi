%% This is file 'webofc.cls'
%%
%%
%% \CharacterTable
%%  {Upper-case    \A\B\C\D\E\F\G\H\I\J\K\L\M\N\O\P\Q\R\S\T\U\V\W\X\Y\Z
%%   Lower-case    \a\b\c\d\e\f\g\h\i\j\k\l\m\n\o\p\q\r\s\t\u\v\w\x\y\z
%%   Digits        \0\1\2\3\4\5\6\7\8\9
%%   Exclamation   \!     Double quote  \"     Hash (number) \#
%%   Dollar        \$     Percent       \%     Ampersand     \&
%%   Acute accent  \'     Left paren    \(     Right paren   \)
%%   Asterisk      \*     Plus          \+     Comma         \,
%%   Minus         \-     Point         \.     Solidus       \/
%%   Colon         \:     Semicolon     \;     Less than     \<
%%   Equals        \=     Greater than  \>     Question mark \?
%%   Commercial at \@     Left bracket  \[     Backslash     \\
%%   Right bracket \]     Circumflex    \^     Underscore    \_
%%   Grave accent  \`     Left brace    \{     Vertical bar  \|
%%   Right brace   \}     Tilde         \~}
\def\filedate{2016/04/05}
\def\fileversion{2.0}
\NeedsTeXFormat{LaTeX2e}[1995/12/01]
\ProvidesClass{webofc}
              [\filedate\space v\fileversion\space
              ^^JLaTeX Class for the Web of Conference Journal
              (EDP Sciences)]
%
\LoadClass[fleqn]{article}
%
%%%%%%%%%%%%%%%%%%%%
%%% Class option %%%
%%%%%%%%%%%%%%%%%%%%
%
\newlength\woc@textwidth
\newlength\woc@textheight
\newlength\woc@left
\newlength\woc@top
\newlength\woc@bottom
%
\newif\if@online
\@onlinefalse
\DeclareOption{online}{\@onlinetrue}
%
\DeclareOption{bio}{%
  \def\woc@name{BIO Web of Conferences}%
}
\DeclareOption{mat}{%
  \def\woc@name{MATEC Web of Conferences}%
}
\DeclareOption{itm}{%
  \def\woc@name{ITM Web of Conferences}%
}
\DeclareOption{e3s}{%
  \def\woc@name{E3S Web of Conferences}%
}
\DeclareOption{shs}{%
  \def\woc@name{SHS Web of Conferences}%
}
\DeclareOption{epj}{%
  \def\woc@name{EPJ Web of Conferences}%
}
\def\wocname#1{\gdef\woc@name{#1}}
\newcommand*\woc@name{The Journal's name}
%
\newif\ifwoc@twocolumn
\DeclareOption{onecolumn}{%
  \woc@twocolumnfalse
  \renewcommand*\woc@column{onecolumn}%
  \renewcommand*\woc@paper{papersize={170mm,250mm}}%
  \setlength\woc@textwidth{130mm}%
  \setlength\woc@textheight{210mm}%
  \setlength\woc@left{20mm}%
  \setlength\woc@top{21mm}%
  \setlength\woc@bottom{18mm}%
  \def\woc@crop{\RequirePackage[cam,center]{crop}}%
}
\DeclareOption{twocolumn}{%
  \woc@twocolumntrue
  \renewcommand*\woc@column{twocolumn}%
  \renewcommand*\woc@paper{a4paper}%
  \setlength\woc@textwidth{170mm}%
  \setlength\woc@textheight{240mm}%
  \setlength\woc@left{20mm}%
  \setlength\woc@top{28mm}%
  \setlength\woc@bottom{16mm}%
  \def\woc@crop{}%
}
\newcommand*\woc@column{}
\newcommand*\woc@paper{}
%
\DeclareOption{ascii}{%
  \def\woc@input{\RequirePackage[ascci]{inputenc}}%
}
\DeclareOption{latin1}{%
  \def\woc@input{\RequirePackage[latin1]{inputenc}}%
}
\DeclareOption{latin9}{%
  \def\woc@input{\RequirePackage[latin9]{inputenc}}%
}
\DeclareOption{ansinews}{%
  \def\woc@input{\RequirePackage[ansinews]{inputenc}}%
}
\DeclareOption{applemac}{%
  \def\woc@input{\RequirePackage[applemac]{inputenc}}%
}
\DeclareOption{utf8}{%
  \def\woc@input{\RequirePackage[utf8]{inputenc}}%
}
%
\DeclareOption{anglais}{%
  \def\woc@language{\RequirePackage[english]{babel}}%
}
\DeclareOption{francais}{%
  \def\woc@language{\RequirePackage[english,frenchb]{babel}}%
}
%
\DeclareOption{showoverfull}{\setlength\overfullrule{0pt}}
\DeclareOption{hideoverfull}{\setlength\overfullrule{5pt}}
%
\DeclareOption{bibnumber}{%
  \def\woc@biblio{%
    \bibpunct{[}{]}{,}{n}{}{}%
    \def\@biblabel##1{[##1]}%
    \bibliographystyle{woc}%
    \def\bibfont{\@BibliographyFont}%
    \setlength{\bibsep}{1pt}%
  }
}
\DeclareOption{bibauthoryear}{%
  \def\woc@biblio{%
    \bibpunct{(}{)}{;}{a}{}{,}%
    \bibliographystyle{woc}%
    \def\bibfont{\@BibliographyFont}%
    \setlength{\bibsep}{1pt}%
  }%
}
%
\ExecuteOptions{anglais,onecolumn,online,utf8,showoverfull,bibnumber}
%
\ProcessOptions
%
\if@online\else
\woc@crop
\fi
%
\RequirePackage{calc}
\RequirePackage[T1]{fontenc}
\woc@input % input method (RequirePackage[<method>]{inputenc})
\RequirePackage[official]{eurosym}
% one column = 81.5mm, columnsep = 6mm => textwidth = 84x2+7 = 170mm
\ifx\pdfoutput\undefined
  \newcount\pdfoutput
\fi
\ifnum\pdfoutput=\z@
  \newcommand\woc@driver{dvips}
\else
  \newcommand\woc@driver{pdftex}
\fi
\RequirePackage[\woc@driver, \woc@paper, \ifwoc@twocolumn\woc@column\fi, twoside,
                top=\woc@top, bottom=\woc@bottom, heightrounded,
                left=\woc@left, textwidth=\woc@textwidth, columnsep=7mm,
                marginparwidth=0in, marginparsep=0in,
                headheight=20.5pt,headsep=16.5pt, nofoot]{geometry}
\RequirePackage[tbtags]{amsmath}
\RequirePackage{amssymb}
\RequirePackage{fancyhdr}
\RequirePackage{cuted}
%
\let\@setmarks\relax
\RequirePackage{graphicx}
\RequirePackage{url, enumerate}
\RequirePackage[sort&compress]{natbib}
\woc@biblio
\RequirePackage{xspace}
\woc@language
%
%%%%%%%%%%%%%%%%%
%%% Registers %%%
%%%%%%%%%%%%%%%%%
%
\def\@fnsymbol#1{\ensuremath{\ifcase#1\or*\or{*}{*}\or{*}{*}{*}%
   \or {*}{*}{*}{*}\or \dagger\or \ddagger\or
   \mathchar "278\or \mathchar "27B\or \|\or **\or \dagger\dagger
   \or \ddagger\ddagger \else\@ctrerr\fi}}
\newcommand\fnsep{\unskip$^,$}
%
\newcounter{@institutecpt}
%
\newlength{\@figgap}
\setlength{\@figgap}{10pt}
%
\newlength{\@floatheight}
%
\setlength\textfloatsep{20\p@ \@plus 2\p@ \@minus 4\p@}
\setlength\dbltextfloatsep{20\p@ \@plus 2\p@ \@minus 4\p@}
\setlength\@fptop{0\p@}
\setlength\@fpsep{12\p@ \@plus 2\p@ \@minus 2\p@}
\setlength\@dblfptop{0\p@}
\setlength\@dblfpsep{12\p@ \@plus 2\p@ \@minus 2\p@}
\setcounter{topnumber}{4}
\renewcommand\topfraction{.9}
\setcounter{bottomnumber}{2}
\renewcommand\bottomfraction{.7}
\setcounter{totalnumber}{6}
\renewcommand\textfraction{.1}
\renewcommand\floatpagefraction{.85}
\setcounter{dbltopnumber}{3}
\renewcommand\dbltopfraction{.85}
\renewcommand\dblfloatpagefraction{.85}
%%%%%%%%%%%%%%%%%%%%%%%%
%%% Predefined names %%%
%%%%%%%%%%%%%%%%%%%%%%%%
%
\addto\captionsfrench{%
  \def\proofname{Preuve}%
  \def\andname{\unskip, }%
  \def\andlastname{\unskip, et }%
  \def\andtwoname{\unskip{} et }%
  \def\acknowledgementname{Remerciements}%
  \def\tablename{Tableau}%
  \def\correspondingauthorname{Auteur à contacter}%
  \def\@datereceiptname{Les dates de réception et d'acceptation}%
  \def\@dateacceptancename{seront indiquées par l'éditeur.}%
  \let\listFB\listORI \def\itemize{%
    \ifnum \@itemdepth >\thr@@\@toodeep \else \advance\@itemdepth\@ne
    \edef\@itemitem{labelitem\romannumeral\the\@itemdepth}%
    \expandafter \list \csname\@itemitem\endcsname {%
      \setlength{\topsep}{4\p@ \@plus2\p@ \@minus\p@}%
      \setlength{\partopsep}{0pt}%
      \setlength{\itemsep}{0pt}%
      \setlength{\labelwidth}{9pt}%
      \setlength{\labelsep}{0pt}%
      \setlength{\itemindent}{0pt}%
      \setlength{\leftmargin}{9pt}%
      \def\makelabel##1{\rlap{##1}\hss}%
    }%
    \fi }%
} \addto\captionsenglish{%
  \def\proofname{Proof}%
  \def\andname{\unskip, }%
  \def\andlastname{\unskip, and }% was {, and }
  \def\andtwoname{\unskip{} and }%
  \def\acknowledgementname{Acknowledgements}%
  \def\tablename{Table}%
  \def\correspondingauthorname{Corresponding author}%
  \def\@datereceiptname{The date of receipt and acceptance}%
  \def\@dateacceptancename{will be inserted by the editor.}%
  \renewcommand\labelitemi{\textbullet}%
  \renewcommand\labelitemii{--}%
  \renewcommand\labelitemiii{--}%
  \renewcommand\labelitemiv{--}%
  \def\itemize{%
    \ifnum \@itemdepth >\thr@@\@toodeep \else \advance\@itemdepth\@ne
    \edef\@itemitem{labelitem\romannumeral\the\@itemdepth}%
    \expandafter \list \csname\@itemitem\endcsname {%
      \setlength{\topsep}{4\p@ \@plus2\p@ \@minus\p@}%
      \setlength{\partopsep}{0pt}%
      \setlength{\itemsep}{0pt}%
      \setlength{\labelwidth}{9pt}%
      \setlength{\labelsep}{0pt}%
      \setlength{\itemindent}{0pt}%
      \setlength{\leftmargin}{9pt}%
      \def\makelabel##1{\rlap{##1}\hss}%
    }%
    \fi }%
} 
\newcommand\CopyrightTitlename{%
  \copyright{} Owned by the authors, published by EDP Sciences, \number\year %
} 
%
%%%%%%%%%%%%%%%%%%%%%%%%%
%%%% Font definitions %%%
%%%%%%%%%%%%%%%%%%%%%%%%%
%
\newcommand\@HeadFont{\normalfont\small}
\newcommand\@HeadPageFont{\normalfont\normalsize}
\newcommand\@TitleFont{\normalfont\fontsize{14pt}{16pt}\sffamily\bfseries}
\newcommand\@SubtitleFont{\normalfont\fontsize{12pt}{14pt}\sffamily\bfseries}
\newcommand\@AuthorFont{\normalfont\normalsize}
\newcommand\@FirstNameAuthorFont{\itshape}
\newcommand\@LastNameAuthorFont{\bfseries}
\newcommand\@InstituteFont{\normalfont\small}
\newcommand\@ThanksFont{\normalfont\fontsize{8pt}{9.6pt}\selectfont}
\newcommand\@ThanksEmail{\normalfont\fontsize{8pt}{9.6pt}\selectfont}
\newcommand\@FootnoteFont{\normalfont\small}
\newcommand\@EmailFont{\normalfont\fontsize{8pt}{9.6pt}\sffamily\slshape}
\newcommand\@ResumeTitleFont{\normalfont\small\bfseries}
\newcommand\@ResumeFont{\normalfont\small}
\newcommand\@AbstractTitleFont{\normalfont\small\sffamily\bfseries}
\newcommand\@AbstractFont{\normalfont\small}
\newcommand\@SectionFont{\normalfont\fontsize{12pt}{14pt}\sffamily\bfseries}
\newcommand\@SubsectionFont{\normalfont\normalsize\sffamily\bfseries}
\newcommand\@SubsubsectionFont{%
  \normalfont\normalsize\sffamily\slshape}
\newcommand\@ParagraphFont{\normalfont\normalsize\itshape}
\newcommand\@BibliographyFont{\normalfont\normalsize}
\newcommand\@TitleTheoremFont{\normalfont\normalsize\bfseries}
\newcommand\@TitleAcknowledgementFont{\fontsize{9pt}{10.8pt}\itshape}
\newcommand\@TextAcknowledgementFont{%
  \normalfont\fontsize{9pt}{10.8pt}\selectfont}
\newcommand\@CaptionFont{\normalfont\small}
\newcommand\@CaptionTitleFont{\bfseries}
\newcommand\@DatesFont{%
  \normalfont\fontsize{9pt}{10.8pt}\itshape
  \raggedleft
}
\newcommand\@CopyrightFont{\fontsize{8pt}{9.6pt}\itshape}
%
%%%%%%%%%%%%%%%%%%%%%%%%%%
%%% Length definitions %%%
%%%%%%%%%%%%%%%%%%%%%%%%%%
%
\newlength{\betweenumberspace}
\setlength{\betweenumberspace}{6pt}
%
\setlength{\parskip}{0pt plus1pt}
\newlength{\@VSpaceBeforeTitle}
\setlength{\@VSpaceBeforeTitle}{2cm}
\newlength{\@VSpaceAfterTitle}
\setlength{\@VSpaceAfterTitle}{10pt}
\newlength{\@VSpaceBeforeInstitute}
\setlength{\@VSpaceBeforeInstitute}{5pt}
\newlength{\@VSpaceAfterAbstract}
\setlength{\@VSpaceAfterAbstract}{9pt}
\newlength{\@VSpaceBeforeAbstract}
\setlength{\@VSpaceBeforeAbstract}{3pt}
\newlength{\@VSpaceBeforeThanks}
\setlength{\@VSpaceBeforeThanks}{10pt}
\newlength{\@VSpaceBeforeEmail}
\setlength{\@VSpaceBeforeEmail}{10pt}
%
%%%%%%%%%%%%%%%%%%%
%%% header page %%%
%%%%%%%%%%%%%%%%%%%
%
\newcommand*\idline[1]{\renewcommand*\woc@idline{\woc@name~#1}}
%\newcommand*\woc@idline{\woc@name~will be set by the publisher}
\newcommand*\woc@idline{}
%
\newcommand*\doi[1]{%
  \renewcommand*\woc@doi{DOI: #1}%
}
\newcommand*\woc@doi{DOI: will be set by the publisher}
%
\def\woctitle#1{\gdef\@woctitle{#1}}
\def\@woctitle{Give the exact title of the conference}
%
\fancypagestyle{firstpage}{%
  \fancyhf{} % clear all
  \renewcommand{\headrulewidth}{0pt}%
  \renewcommand{\footrulewidth}{0pt}%
  \fancyhead[L]{%
    \minipage[t][\baselineskip]{\linewidth}
      \@HeadFont
\ifx\woc@idline\empty
\else
      \woc@idline\\
      \woc@doi\\
      \CopyrightTitlename
\fi
    \endminipage
  }%
}
\fancypagestyle{otherpage}{%
  \fancyhf{} % clear all
  \renewcommand{\headrulewidth}{0pt}%
  \renewcommand{\footrulewidth}{0pt}%
  \fancyhead[CE]{%
    \@HeadFont
\ifx\woc@idline\empty
\else
    \woc@name
\fi
  }%
  \fancyhead[CO]{%
    \@HeadFont
\ifx\woc@idline\empty
\else
    \@woctitle
\fi
  }%
}
\pagestyle{otherpage}
%
\clubpenalty=10000
\widowpenalty=10000
%
\flushbottom
\sloppy
%
%
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
%%% One column introduction %%%
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
%
\newcommand\headnote[1]{\def\@headnote{#1}}
\renewcommand\title[1]{\def\@title{#1}}
\newcommand\titlerunning[1]{\def\@titlerunning{#1}}
\newcommand\subtitle[1]{\def\@subtitle{#1}}
\newcommand\authorrunning[1]{\def\@authorrunning{#1}}
\newcommand\offprints[1]{\def\@offprints{#1}}
\newcommand\institute[1]{\def\@institute{#1}}
\renewcommand\abstract[1]{\def\@abstract{#1}}
\newcommand\resume[1]{\def\@resume{#1}}
\def\UrlFont{}
\newcommand\inst[1]{\unskip$^{#1}$\ignorespaces}
\DeclareUrlCommand\@email{\urlstyle{same}} % \@e-mail: \@email
\newcommand\email[1]{%
e-mail: \protect\@email{#1}%
}
\renewcommand\date[2]{
  \def\@datereceipt{#1}%
  \def\@dateacceptance{#2}%
}
%
\def\@headnote{}
\def\@title{%
  \ClassError{webofc}
             {No title given}
             {You have to define a title with \noexpand\title{your title}.}
}
\def\@titlerunning{}
\def\@subtitle{}
\def\@author{%
  \ClassError{webofc}
             {No author given}
             {You have to indicate author(s) with
               \noexpand\author{author name \noexpand\and\space author
               name ...}.}
}
\def\@authorrunning{}
\def\@offprints{}
\def\@institute{}
\def\@abstract{%
  \ClassError{webofc}
             {No abstract given}
             {You have to define an abstract with
               \noexpand\abstract{your abstract (in english)}.}%
}
\def\@datereceipt{\@datereceiptname}
\def\@dateacceptance{\@dateacceptancename}
%
% Parse authors, institutes, thanks, and email 
%
\def\firstname#1{{\@FirstNameAuthorFont#1\protect}}
\def\lastname#1{{#1}}
\def\@parseauthor{%
  \count@ \z@
  \expandafter\@countand\@author\and\@nil\and
  \gdef\@headerauthor{}%
  \def\@typeauthor{0}%
  \ifnum\count@=1\relax
    % no \and to process
    % one author in header
    \def\@typeauthor{1}%
  \else\ifnum\count@=2\relax
    % juste one \and
    \let\@andlastauthor\andtwoname
    % two author in header
    \def\@typeauthor{2}%
  \else % > 2
    % real list
    \let\@andauthor\andname
    \let\@andlastauthor\andlastname
    % one author et al. in header
    \def\@typeauthor{3}%
  \fi\fi
  \@AuthorFont
  \expandafter\@typesetauthor\@author\and\@nil\and
}
\def\@countand#1\and{%
  \def\@rg{#1}%
  \ifx\@rg\@nnil\else
    \advance\count@ \@ne
    \expandafter\@countand
  \fi
}
\def\@typesetauthor#1\and{%
  \def\@rg{#1}%
  \advance\count@ \m@ne
  \ifnum\count@>-1\relax
    % for header
    \ifnum\@typeauthor>0
      \@extractauthor
      \gdef\@headerauthor{\@currentheaderauthor}%
      \ifcase\@typeauthor
        % 0 impossible
        \or
        % 1 -> nothing to do
          % no more author
          \def\@typeauthor{0}%
        \or
        % 2 -> and
          \protected@xdef\@headerauthor{%
            \@headerauthor\space and
          }
          % add the next author
          \def\@typeauthor{-1}%
        \or
        % 3 -> et al.
          \protected@xdef\@headerauthor{%
            \@headerauthor\space et al.%
          }
          % no more author
          \def\@typeauthor{0}%
      \fi
    \else\ifnum\@typeauthor<0
      \@extractauthor
      \protected@xdef\@headerauthor{%
        \@headerauthor \@currentheaderauthor
      }
    \fi\fi
    % for first page
    \ifnum\count@=0\relax
       #1
    \else\ifnum\count@=1\relax
      #1\@andlastauthor
    \else
      #1\@andauthor
    \fi\fi
    \expandafter\@typesetauthor
  \fi
}
\def\@extractauthor{%
  % examine \@rg = <first name> <name>
  % exctract first letter of <first name>
  % and add <name>. Result in \@currentheaderauthor
  \def\@currentheaderauthor{}%
  \expandafter\@@extractauthor\@rg\inst\thanks\@nil
}
\def\@@extractauthor#1#2 #3\inst#4\thanks#5\@nil{%
  \def\@currentheaderauthor{#1 #3}%
}
%
\newcount\woc@nbinstitutes
\def\@parseinstitute{\par%
  \count@ \z@
  \expandafter\@countand\@institute\and\@nil\and
  \setcounter{@institutecpt}{0}%
  \@InstituteFont
  \global\woc@nbinstitutes \z@
  \expandafter\@typesetinstitute\@institute\and\@nil\and
}
\def\@typesetinstitute#1\and{%
  \def\@rg{#1}%
  \advance\count@ \m@ne
  \global\advance\woc@nbinstitutes \@ne
  \refstepcounter{@institutecpt}%
  \ifnum\count@>-1\relax
    \setbox0=\hbox{\@textsuperscript{\normalfont\the@institutecpt}}
    \hangindent\wd0
    \noindent\hbox to\wd0{\@textsuperscript{\normalfont\the@institutecpt}}%
    \ignorespaces#1\par
    \expandafter\@typesetinstitute
  \fi
}
%
\newenvironment{abstract@margin}
{\vspace*{\@VSpaceBeforeAbstract}%
  \list{}{%
    \listparindent 0em
    \itemindent    \listparindent
    \leftmargin    1.5cm
    \rightmargin   \leftmargin
    \parsep        \z@ \@plus\p@
  }%
  \item\relax
}
{\endlist\vspace*{\@VSpaceAfterAbstract}}
%
% real command to typeset introduction
%
\renewcommand*\maketitle{%
  \thispagestyle{firstpage}
  \ifwoc@twocolumn
  \begingroup
    \renewcommand*\thefootnote{\@fnsymbol\c@footnote}%
    \def\switch@fnmark{\def\thefootnote{\text{\alph{footnote}}}}
    \def\@makefnmark{\@textsuperscript{\scriptsize\@thefnmark}}
    \begin{strip}
      \woc@maketitle
    \end{strip}
    \@thanks
  \endgroup
  \else
    \begingroup
      \parindent=0pt
      \renewcommand*\thefootnote{\@fnsymbol\c@footnote}%
      \def\switch@fnmark{\def\thefootnote{\text{\alph{footnote}}}}
      \def\@makefnmark{\@textsuperscript{\scriptsize\@thefnmark}}
      \let\thanks\footnote
      \woc@maketitle
      \endgroup \fi     
      \setcounter{footnote}{0}%
}
\newcommand\woc@maketitle{%
    \par\vspace*{\@VSpaceBeforeTitle}
      % title
      {\@TitleFont \@title\par}
      \vspace*{\@VSpaceAfterTitle}
      % subtitle
      \ifx\relax\@subtitle\relax\else
        {\@SubtitleFont \@subtitle\par}
        \vspace*{\@VSpaceAfterTitle}
      \fi
      %author
      % process \and display author(s) and build header
      %\switch@fnmark
      %\protected@xdef\@thanks{\@thanks\protect\switch@fnmark}
      \protected@xdef\@thanks{\@thanks}
      %\setcounter{footnote}{0}
      \@parseauthor
      % display institute(s)
      \ifx\@institute\empty
      \else
        \par\vspace*{\@VSpaceBeforeInstitute}
        \@FootnoteFont
        \@parseinstitute
      \fi
    % abstract
    \begin{abstract@margin}
      \@AbstractTitleFont
      \abstractname.\ 
      \@AbstractFont
      \@abstract
    \end{abstract@margin}
}
%
%%%%%%%%%%%%%%%%%%%%%%%%%%%
%%% Automatic Balancing %%%
%%%%%%%%%%%%%%%%%%%%%%%%%%%
%
% LOCAL PATCH: Original webofc.cls injects a strip+vstretch column-balancer
% at end-of-document; on a tightly-packed page that overflows to a phantom
% blank page. Disabled for our build; restore for submission if EPJ requires.
%\AtEndDocument{%
%  \ifwoc@twocolumn
%  \begin{strip}
%    \null \par
%  \end{strip}
%  \par\vspace*{\stretch{1}}
%  \fi
%}
%
%%%%%%%%%%%%%%%%%%%%%%
%%% section macros %%%
%%%%%%%%%%%%%%%%%%%%%%
%
\def\sectcounterend{}
\def\@seccntformat#1{\csname the#1\endcsname\sectcounterend
\hskip\betweenumberspace}
\def\Special@bs{\let\\\newline}
\renewcommand\part{%
  \ClassError{webofc}
             {Macro \noexpand\part\space isn't allowed}
             {You can use only macros from  \noexpand\section\space to
             \noexpand\paragraph}%
}
\renewcommand\section{%
  \@startsection {section}{1}{\z@}%
  {-16dd plus-4pt minus-4pt}%
  {8dd plus4pt minus4pt}%
  {\@SectionFont\raggedright}%
}
\renewcommand\subsection{%
  \@startsection{subsection}{2}{\z@}%
  {-16dd plus-4pt minus-4pt}%
  {8dd plus4pt minus4pt}%
  {\@SubsectionFont\raggedright}%
}
\renewcommand\subsubsection{%
  \@startsection{subsubsection}{3}{\z@}%
  {-16dd plus-4pt minus-4pt}%
  {8dd plus4pt minus4pt}%
  {\@SubsubsectionFont\raggedright}%
}
\renewcommand\paragraph{%
  \@startsection{paragraph}{4}{\z@}%
  {-13dd plus-4pt minus-4pt}%
  {5.5pt}%
  {\@ParagraphFont\raggedright}%
}
\renewcommand\subparagraph{%
  \ClassError{webofc}
             {Macro \noexpand\subparagraph\space isn't allowed}
             {You can use only macros from  \noexpand\section\space to
             \noexpand\paragraph}
}
%
%%%%%%%%%%%%%%%%%%%%%%
%%% Aknowledgement %%%
%%%%%%%%%%%%%%%%%%%%%%
%
\newenvironment{acknowledgement}
{%\section*{\acknowledgementname}%
\noindent\ignorespaces\@TextAcknowledgementFont}{}
%
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
%%% modified standard description environment %%%
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
%
\renewenvironment{description}[1][]
{%
  \list{}
       {%
         \setlength{\leftmargin}{0pt}%
         \setlength{\labelsep}{0pt}%
         \ifnum\@listdepth<2
           \setlength{\labelwidth}
                     {\widthof{\descriptionlabel{#1}}}%
         \else
           \labelwidth\z@
         \fi
         \setlength{\itemindent}{\labelwidth}%
         \let\makelabel\descriptionlabel
       }%
}
{\endlist}
\renewcommand*\descriptionlabel[1]{%
  \normalfont\bfseries #1
  \hss % add 
}
%
%%%%%%%%%%%%%%
%%% Floats %%%
%%%%%%%%%%%%%%
%
% Float placement
%
\def\fps@figure{tbp}
\def\fps@table{tbp}
%
% Float Body
%
% figure
\renewenvironment{figure}
  {\@float{figure}}
  {\end@float}
\renewenvironment{figure*}
  {\@dblfloat{figure}}
  {\end@dblfloat}
% table
\renewenvironment{table}
               {\@float{table}}
               {\end@float}
\renewenvironment{table*}
               {\@dblfloat{table}}
               {\end@dblfloat}
%
\setlength\abovecaptionskip{10\p@}
\setlength\belowcaptionskip{10\p@}
\def\string@table{table}
%
% Captions
%
\newcommand\@FloatCounterEnd{}
\newcommand\@FloatCounterSep{. }
%
\renewcommand\@makecaption[2]{%
  \par\addvspace\abovecaptionskip
  \ifx\@captype\string@table
    % Table: centering caption
    \begingroup
      \centering
      \@CaptionFont{%
        \@CaptionTitleFont #1%
        \@FloatCounterEnd\@FloatCounterSep
      }\ignorespaces#2%
      \par
    \endgroup
  \else
    % Figure (and others?): justified
    \begingroup
      \@CaptionFont{%
        \@CaptionTitleFont #1%
        \@FloatCounterEnd\@FloatCounterSep
      }\ignorespaces#2%
      \par
    \endgroup
    %
  \fi
  \par\addvspace\belowcaptionskip
}
\newcommand\@makesidecaption[2]{%
  \raisebox{0.5\@floatheight}{%
    \parbox{\@tempdima}{%
      \raggedright
      \@CaptionFont{%
        \@CaptionTitleFont #1%
        \@FloatCounterEnd\@FloatCounterSep
      }\ignorespaces#2%
    }%
  }%
}
\long\def\sidecaption#1\caption{%
  \setbox\@tempboxa=\hbox{\unskip#1\unskip\ignorespaces}%
  \setlength{\@floatheight}{\ht\@tempboxa+\dp\@tempboxa}%
  \setlength{\@tempdima}{\columnwidth-\@figgap-\wd\@tempboxa}%
  \ifdim\@tempdima<3cm
    \ClassWarning{webofc}{%
      \string\sidecaption: No sufficient room for the legend;
      using normal \string\caption.
    }%
    \leavevmode
    \box\@tempboxa
    \let\@capcommand=\@caption
  \else
    \let\@capcommand=\@sidecaption
    \leavevmode
    \box\@tempboxa\hspace*{\@figgap}%
  \fi
  \refstepcounter\@captype
  \@dblarg{\@capcommand\@captype}%
}

\long\def\@sidecaption#1[#2]#3{%
  \addcontentsline{\csname ext@#1\endcsname}
                  {#1}
                  {%
                    \protect\numberline{\csname the#1\endcsname}%
                    {\ignorespaces #2}%
                  }%
  \begingroup
    \@parboxrestore
    \@makesidecaption{\csname fnum@#1\endcsname}
                     {\ignorespaces #3}%
    \par
  \endgroup
}
%
\endinput
%%
%% End of file `webofc.cls'
