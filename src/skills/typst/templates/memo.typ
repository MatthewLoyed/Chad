#set page(paper: "us-letter", margin: 1.2in)
#set text(font: "Libertinus Serif", size: 11pt)

#align(right)[
  *MEMO*
]

#v(1em)

*To:* Recipient Name \
*From:* Sender Name \
*Date:* #datetime.today().display() \
*Subject:* Subject line here

#line(length: 100%)

#v(0.5em)

Body paragraph one. Replace this template content before compiling.

#v(0.5em)

- Bullet action item
- Second item

#v(1em)

#align(left)[
  #text(size: 9pt)[_End of memo_]
]
