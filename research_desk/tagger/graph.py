"""Assemble the v2 row-graph from 8 node modules.

v1 → v2 변경:
- canonicalize / validate / enrich → resolve_krx (1 노드)
- oos_gate 라벨 'canonicalize' → 'resolve_krx'
- 총 10 노드 → 8 노드
"""
from __future__ import annotations

from functools import partial

from langgraph.graph import END, START, StateGraph

from research_desk.domain.stocks import StockList
from research_desk.tagger.nodes.decide_status import decide_status
from research_desk.tagger.nodes.extract_pdf import extract_pdf
from research_desk.tagger.nodes.llm_extract import llm_extract
from research_desk.tagger.nodes.mark_oos_reason import mark_oos_reason
from research_desk.tagger.nodes.oos_gate import oos_gate
from research_desk.tagger.nodes.resolve_krx import resolve_krx
from research_desk.tagger.nodes.status_oos import status_oos
from research_desk.tagger.nodes.status_unreadable import status_unreadable
from research_desk.tagger.nodes.write import write
from research_desk.tagger.state import RowState


def build_graph(client, sb, *, krx: StockList, dry_run: bool, taxonomy_version: str):
    g = StateGraph(RowState)

    g.add_node("extract_pdf", extract_pdf)
    g.add_node("llm_extract", partial(llm_extract, client=client))
    g.add_node("mark_oos_reason", mark_oos_reason)
    g.add_node("status_oos", status_oos)
    g.add_node("status_unreadable", status_unreadable)
    g.add_node("resolve_krx", partial(resolve_krx, krx=krx))
    g.add_node("decide_status", decide_status)
    g.add_node("write", partial(write, sb=sb, dry_run=dry_run, taxonomy_version=taxonomy_version))

    g.add_edge(START, "extract_pdf")
    g.add_edge("extract_pdf", "llm_extract")
    g.add_conditional_edges(
        "llm_extract",
        partial(oos_gate, krx=krx),
        {
            "mark_oos_reason":   "mark_oos_reason",
            "status_unreadable": "status_unreadable",
            "resolve_krx":       "resolve_krx",
        },
    )
    g.add_edge("mark_oos_reason", "status_oos")
    g.add_edge("status_oos", "write")
    g.add_edge("status_unreadable", "write")
    g.add_edge("resolve_krx", "decide_status")
    g.add_edge("decide_status", "write")
    g.add_edge("write", END)

    return g.compile()
