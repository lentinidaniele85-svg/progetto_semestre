import asyncio
from dotenv import load_dotenv
load_dotenv()

from agents.graph import build_graph
from langgraph.checkpoint.memory import MemorySaver

async def main():
    checkpointer = MemorySaver()
    graph = build_graph(mode="autonomous", checkpointer=checkpointer)
    config = {"configurable": {"thread_id": "test_thread"}}
    state = {
        "user_input": "Voglio ottimizzare l'impatto ambientale di un lotto di 500 kg di profilati di alluminio, con un trasporto aggiuntivo di 1500 km effettuato tramite nave transoceanica cargo.",
        "thought_log": [],
        "current_phase": "init",
        "interview_attempt_count": 0,
        "assumptions_list": []
    }
    
    print("Inizio esecuzione workflow...")
    try:
        async for event in graph.astream(state, config, stream_mode="values"):
            if "current_phase" in event:
                print(f"Fase corrente: {event['current_phase']}")
            
            if "bom" in event and event["bom"]:
                print("\nBOM Generata:")
                for item in event["bom"]:
                    print(f" - {item.get('name')}: {item.get('material')} ({item.get('weight_kg')} kg) -> {item.get('manufacturing_process')} [is_material_only={item.get('is_material_only', False)}]")
            
            if "lca_results" in event and event["lca_results"]:
                print("\nRisultati LCA Base:")
                for res in event["lca_results"]:
                     print(f" - {res.get('component_name')}: {res.get('original_scores', {}).get('environmental_impact', 0):.2f} kg CO2eq")
                     
            if "mcda_scores" in event and event["mcda_scores"]:
                print("\nOttimizzazioni suggerite:")
                for score in event["mcda_scores"]:
                    best = score.get('best_alternative')
                    if best:
                        print(f" - {score.get('component_name')}: Suggerisce {best.get('name')} (Riduzione {best.get('impact_reduction_pct', 0):.1f}%)")
    except Exception as e:
        print(f"Errore durante l'esecuzione: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
