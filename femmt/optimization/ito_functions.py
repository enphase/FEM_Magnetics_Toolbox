# python libraries
import shutil
import os
from typing import List, Tuple

# 3rd party libraries

# femmt libraries
from femmt.optimization.integrated_transformer_dtos import *
import femmt.functions_reluctance as fr
import femmt.functions as ff
import femmt as fmt

def dto_list_to_vec(dto_list: List[ItoSingleResultFile]) -> Tuple:
    """
    Brings a list of dto-objects to two lists

    Use case is to bring the pareto-front into two vectors for further calculations

    :param dto_list: list of ItoSingleResultFile-DTOs
    :type dto_list: List[ItoSingleResultFile]

    """

    for dto in dto_list:
        x_pareto_vec = dto.core_2daxi_total_volume
        y_pareto_vec = dto.total_loss

    vector_to_sort = np.array([x_pareto_vec, y_pareto_vec])

    # sorting 2d array by 1st row
    # https://stackoverflow.com/questions/49374253/sort-a-numpy-2d-array-by-1st-row-maintaining-columns
    sorted_vector = vector_to_sort[:, vector_to_sort[0].argsort()]
    x_pareto_vec = sorted_vector[0]
    y_pareto_vec = sorted_vector[1]

    return x_pareto_vec, y_pareto_vec

def integrated_transformer_fem_simulation_from_result_dto(config_dto: ItoSingleInputConfig,
                                                          dto: ItoSingleResultFile,
                                                          fem_working_directory: str,
                                                          fundamental_frequency: float,
                                                          i_peak_1: float,
                                                          i_peak_2: float,
                                                          phase_deg_1: float,
                                                          phase_deg_2: float,
                                                          visualize: bool = False):
    # 1. chose simulation type
    geo = fmt.MagneticComponent(component_type=fmt.ComponentType.IntegratedTransformer,
                                working_directory=fem_working_directory,
                                silent=True)

    window_h = dto.window_h_bot + dto.window_h_top + dto.core_inner_diameter / 4

    core_dimensions = fmt.dtos.SingleCoreDimensions(core_inner_diameter=dto.core_inner_diameter,
                                                    window_w=dto.window_w,
                                                    window_h=window_h)

    core = fmt.Core(core_type=fmt.CoreType.Single,
                    core_dimensions=core_dimensions,
                    material=dto.core_material,
                    temperature=config_dto.temperature,
                    frequency=fundamental_frequency,
                    permeability_datasource=fmt.MaterialDataSource.ManufacturerDatasheet,
                    permittivity_datasource=fmt.MaterialDataSource.ManufacturerDatasheet)

    geo.set_core(core)

    table_length = dto.core_inner_diameter / 2 + dto.window_w - dto.air_gap_middle
    # 2.1 set stray path parameters
    stray_path = fmt.StrayPath(start_index=0,
                               length=table_length)
    geo.set_stray_path(stray_path)

    # Note: bot air gap needs to be subtracted, top air gap needs to be added
    air_gap_top_position_percent = \
        (dto.window_h_bot + dto.core_inner_diameter / 4 + dto.air_gap_top / 2) / window_h * 100
    air_gap_bot_position_percent = (dto.window_h_bot - dto.air_gap_bot / 2) / window_h * 100

    # 3. set air gap parameters
    air_gaps = fmt.AirGaps(fmt.AirGapMethod.Percent, core)
    air_gaps.add_air_gap(fmt.AirGapLegPosition.CenterLeg, dto.air_gap_bot, air_gap_bot_position_percent)
    air_gaps.add_air_gap(fmt.AirGapLegPosition.CenterLeg, dto.air_gap_top, air_gap_top_position_percent)
    geo.set_air_gaps(air_gaps)

    # 4. set insulations
    insulation = fmt.Insulation()
    insulation.add_core_insulations(0.001, 0.001, 0.001, 0.001)
    insulation.add_winding_insulations([0.0002, 0.0002], 0.0001)
    geo.set_insulation(insulation)

    # 5. create winding window and virtual winding windows (vww)
    # For an integrated transformer it is not necessary to set horizontal and vertical split factors
    # since this is determined by the stray_path
    winding_window = fmt.WindingWindow(core, insulation, stray_path, air_gaps)
    top, bot = winding_window.split_window(fmt.WindingWindowSplit.HorizontalSplit)

    # 6. set conductor parameters
    primary_litz = ff.litz_database()[dto.primary_litz_wire]
    secondary_litz = ff.litz_database()[dto.secondary_litz_wire]

    winding1 = fmt.Conductor(0, fmt.Conductivity.Copper)
    winding1.set_litz_round_conductor(primary_litz["conductor_radii"], primary_litz["strands_numbers"],
                                      primary_litz["strand_radii"], None, fmt.ConductorArrangement.Square)

    winding2 = fmt.Conductor(1, fmt.Conductivity.Copper)
    winding2.set_litz_round_conductor(secondary_litz["conductor_radii"], secondary_litz["strands_numbers"],
                                      secondary_litz["strand_radii"], None, fmt.ConductorArrangement.Square)

    # 7. add conductor to vww and add winding window to MagneticComponent
    top.set_interleaved_winding(winding1, dto.n_p_top, winding2, dto.n_s_top,
                                fmt.InterleavedWindingScheme.HorizontalAlternating,
                                0.0005)
    bot.set_interleaved_winding(winding1, dto.n_p_bot, winding2, dto.n_s_bot,
                                fmt.InterleavedWindingScheme.HorizontalAlternating,
                                0.0005)
    geo.set_winding_window(winding_window)

    # 8. start simulation with given frequency, currents and phases
    geo.create_model(freq=fundamental_frequency, visualize_before=visualize)
    geo.single_simulation(freq=fundamental_frequency,
                          current=[i_peak_1, i_peak_2],
                          phi_deg=[phase_deg_1, phase_deg_2],
                          show_results=visualize)

    return geo

def integrated_transformer_fem_simulations_from_result_dtos(config_dto: ItoSingleInputConfig,
                                                            simulation_dto_list: List[ItoSingleResultFile],
                                                            visualize: bool = False,
                                                            ):
    ito_target_and_fixed_parameters_dto = fmt.optimization.ito_optuna.ItoOptuna.calculate_fix_parameters(config_dto)

    time_extracted, current_extracted_1_vec = fr.time_vec_current_vec_from_time_current_vec \
        (config_dto.time_current_1_vec)
    time_extracted, current_extracted_2_vec = fr.time_vec_current_vec_from_time_current_vec \
        (config_dto.time_current_2_vec)
    fundamental_frequency = int(1 / time_extracted[-1])

    phase_deg_1, phase_deg_2 = fr.phases_deg_from_time_current(time_extracted, current_extracted_1_vec, current_extracted_2_vec)
    i_peak_1, i_peak_2 = fr.max_value_from_value_vec(current_extracted_1_vec, current_extracted_2_vec)



    for count, dto in enumerate(simulation_dto_list):
        print(f"FEM simulation {count} of {len(simulation_dto_list)}")
        try:
            geo = integrated_transformer_fem_simulation_from_result_dto(config_dto = config_dto,
                                                                        dto=dto,
                                                                        fem_working_directory = ito_target_and_fixed_parameters_dto.fem_working_directory,
                                                                        fundamental_frequency = fundamental_frequency,
                                                                        i_peak_1=i_peak_1,
                                                                        i_peak_2=i_peak_2,
                                                                        phase_deg_1 = phase_deg_1,
                                                                        phase_deg_2 = phase_deg_2,
                                                                        visualize= visualize)

            source_json_file = os.path.join(ito_target_and_fixed_parameters_dto.fem_working_directory, "results", "log_electro_magnetic.json")
            destination_json_file = os.path.join(ito_target_and_fixed_parameters_dto.fem_simulation_results_directory,
                                                f'case_{dto.case}.json')

            shutil.copy(source_json_file, destination_json_file)

        except Exception as e:
            print(f"Exception: {e}")

def integrated_transformer_fem_thermal_simulations_from_result_dtos(config_dto: ItoSingleInputConfig,
                                                            simulation_dto_list: List[ItoSingleResultFile],
                                                            visualize: bool = False,
                                                            ):
    ito_target_and_fixed_parameters_dto = fmt.optimization.ito_optuna.ItoOptuna.calculate_fix_parameters(config_dto)

    time_extracted, current_extracted_1_vec = fr.time_vec_current_vec_from_time_current_vec \
        (config_dto.time_current_1_vec)
    time_extracted, current_extracted_2_vec = fr.time_vec_current_vec_from_time_current_vec \
        (config_dto.time_current_2_vec)
    fundamental_frequency = int(1 / time_extracted[-1])

    phase_deg_1, phase_deg_2 = fr.phases_deg_from_time_current(time_extracted, current_extracted_1_vec,
                                                               current_extracted_2_vec)
    i_peak_1, i_peak_2 = fr.max_value_from_value_vec(current_extracted_1_vec, current_extracted_2_vec)

    for dto in simulation_dto_list:
        try:
            geo = integrated_transformer_fem_simulation_from_result_dto(config_dto=config_dto,
                                                                        dto=dto,
                                                                        fem_working_directory=ito_target_and_fixed_parameters_dto.fem_working_directory,
                                                                        fundamental_frequency=fundamental_frequency,
                                                                        i_peak_1=i_peak_1,
                                                                        i_peak_2=i_peak_2,
                                                                        phase_deg_1=phase_deg_1,
                                                                        phase_deg_2=phase_deg_2,
                                                                        visualize=visualize)


            thermal_conductivity_dict = {
                "air": 0.122,  # potting epoxy resign
                "case": {
                    "top": 0.122,
                    "top_right": 0.122,
                    "right": 0.122,
                    "bot_right": 0.122,
                    "bot": 0.122
                },
                "core": 5,  # ferrite
                "winding": 0.517,  # copper
                "air_gaps": 1.57,
                "insulation": 1.57
            }

            case_gap_top = 0.0004
            case_gap_right = 0.001
            case_gap_bot = 0.005

            case_temperature = 60

            boundary_temperatures = {
                "value_boundary_top": case_temperature,
                "value_boundary_top_right": case_temperature,
                "value_boundary_right_top": case_temperature,
                "value_boundary_right": case_temperature,
                "value_boundary_right_bottom": case_temperature,
                "value_boundary_bottom_right": case_temperature,
                "value_boundary_bottom": case_temperature
            }

            boundary_flags = {
                "flag_boundary_top": 0,
                "flag_boundary_top_right": 0,
                "flag_boundary_right_top": 1,
                "flag_boundary_right": 1,
                "flag_boundary_right_bottom": 1,
                "flag_boundary_bottom_right": 1,
                "flag_boundary_bottom": 1
            }

            # color_scheme = fmt.colors_ba_jonas
            # colors_geometry = fmt.colors_geometry_ba_jonas
            color_scheme = fmt.colors_ba_jonas
            colors_geometry = fmt.colors_geometry_draw_only_lines

            geo.thermal_simulation(thermal_conductivity_dict, boundary_temperatures, boundary_flags, case_gap_top,
                                   case_gap_right,
                                   case_gap_bot, show_results=visualize, visualize_before=False, color_scheme=color_scheme,
                                   colors_geometry=colors_geometry)

            source_json_file = os.path.join(ito_target_and_fixed_parameters_dto.fem_working_directory, "results",
                                            "results_thermal.json")
            destination_json_file = os.path.join(ito_target_and_fixed_parameters_dto.fem_thermal_simulation_results_directory,
                                                 f'case_thermal_{dto.case}.json')

            shutil.copy(source_json_file, destination_json_file)
        except:
            pass