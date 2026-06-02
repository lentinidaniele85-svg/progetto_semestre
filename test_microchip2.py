import asyncio
from dotenv import load_dotenv
load_dotenv()

from agents.graph import build_graph
from langgraph.checkpoint.memory import MemorySaver

async def main():
    checkpointer = MemorySaver()
    graph = build_graph(mode="autonomous", checkpointer=checkpointer)
    config = {"configurable": {"thread_id": "test_microchip_2"}}
    state = {
        "user_input": "Voglio produrre un microchip elettronico in silicio, peso 0.005 kg",
        "thought_log": [],
        "current_phase": "init",
        "interview_attempt_count": 0,
        "assumptions_list": []
    }
    
    print("Inizio esecuzione workflow Microchip...")
    try:
        async for event in graph.astream(state, config, stream_mode="values"):
            if "bom" in event and event["bom"]:
                print("\nBOM Generata:")
                for item in event["bom"]:
                    print(f" - {item.get('name')}: {item.get('material')} ({item.get('weight_kg')} kg) -> {item.get('manufacturing_process')} (db_index={item.get('db_index')})")
            
            if "lca_results" in event and event["lca_results"]:
                print("\nRisultati LCA Base:")
                for res in event["lca_results"]:
                     print(f" - {res.get('component_name')}: {res.get('original_scores', {}).get('environmental_impact', 0):.6f} kg CO2eq")
                     
            if event.get("current_phase") == "complete":
                print("\n--- THOUGHT LOG ---")
                for thought in event.get("thought_log", []):
                    print(thought)
                    
    except Exception as e:
        print(f"Errore durante l'esecuzione: {e}")

if __name__ == "__main__":
    asyncio.run(main())
