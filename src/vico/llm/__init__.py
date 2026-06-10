"""
LLM subsystem — provider abstraction, model registry, and factory.

Entry points
------------
  create_llm_from_config(llm_config)  — build an LLM from ``AgentConfig.llm``
  vico.llm.providers                  — individual provider implementations
  vico.llm.models                     — static model metadata registry
"""

from __future__ import annotations
