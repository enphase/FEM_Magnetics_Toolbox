# Python libraries
import inspect
import os
import json
import itertools
import shutil
import dataclasses
from typing import List, Dict
import time

# 3rd party library import
import numpy as np
import materialdatabase as mdb
from scipy import optimize

# femmt import
import femmt.functions as ff
import femmt.functions_reluctance as fr
import femmt.optimization.functions_optimization as fo
from femmt.optimization.integrated_transformer_dtos import *
import femmt

class MyJSONEncoder(json.JSONEncoder):
    """
    class to transform dicts with numpy arrays to json.
    This class is used as cls=MyJSONEncoder by json.dump

    See also:
    https://python-forum.io/thread-35245.html
    """
    def default(self, o):
        try:
            return o.tolist() # works with any object that has .tolist() method
        except AttributeError:
            pass
        # Let the base class default method raise the TypeError
        return json.JSONEncoder.default(self, o)

def result_file_dict_to_dto(result_file_dict):
    result_file_dto = ItoSingleResultFile(
        case = result_file_dict["case"],
        air_gap_top = result_file_dict["air_gap_top"],
        air_gap_bot= result_file_dict["air_gap_bot"],
        air_gap_middle=result_file_dict["air_gap_middle"],
        n_p_top=int(result_file_dict["n_p_top"]),
        n_p_bot=int(result_file_dict["n_p_bot"]),
        n_s_top=int(result_file_dict["n_s_top"]),
        n_s_bot=int(result_file_dict["n_s_bot"]),
        window_h_top=result_file_dict["window_h_top"],
        window_h_bot=result_file_dict["window_h_bot"],
        window_w=result_file_dict["window_w"],
        core_material=result_file_dict["core_material"],
        core_inner_diameter=result_file_dict["core_inner_diameter"],
        flux_top_max=result_file_dict["flux_top_max"],
        flux_bot_max=result_file_dict["flux_bot_max"],
        flux_stray_max=result_file_dict["flux_stray_max"],
        flux_density_top_max=result_file_dict["flux_density_top_max"],
        flux_density_bot_max=result_file_dict["flux_density_bot_max"],
        flux_density_stray_max=result_file_dict["flux_density_stray_max"],
        p_hyst= result_file_dict["p_hyst"],
        core_2daxi_total_volume=result_file_dict["core_2daxi_total_volume"],
        primary_litz_wire=result_file_dict["primary_litz_wire"],
        secondary_litz_wire=result_file_dict["secondary_litz_wire"],
        primary_litz_wire_loss=result_file_dict["primary_litz_wire_loss"],
        secondary_litz_wire_loss=result_file_dict["secondary_litz_wire_loss"],
        total_loss=result_file_dict["total_loss"]
    )
    return result_file_dto



class IntegratedTransformerOptimization:

    @staticmethod
    def set_up_folder_structure(working_directory: str) -> None:
        """
        Set up the folder structure for the integrated transformer optimization.

        :param working_directory: working directory
        :type working_directory: str
        :return: None
        :rtype: None
        """
        if working_directory is None:
            caller_filename = inspect.stack()[1].filename
            working_directory = os.path.join(os.path.dirname(caller_filename), "integrated_transformer_optimization")

        # generate new and empty working directory
        if not os.path.exists(working_directory):
            os.mkdir(working_directory)


        fem_working_directory = os.path.join(working_directory, '00_femmt_simulation')
        reluctance_model_results_directory = os.path.join(working_directory, "01_reluctance_model_results")
        fem_simulation_results_directory = os.path.join(working_directory, "02_fem_simulation_results")
        fem_thermal_simulation_results_directory = os.path.join(working_directory, "03_fem_thermal_simulation_results")

        os.makedirs(fem_working_directory, exist_ok=True)
        os.makedirs(fem_simulation_results_directory, exist_ok=True)
        os.makedirs(reluctance_model_results_directory, exist_ok=True)
        os.makedirs(fem_thermal_simulation_results_directory, exist_ok=True)


        # integrated_transformer_optimization_input_parameters_file = os.path.join(optimization_working_directory, "optimization_input_parameters.json")

        #ff.create_folders(working_directory, integrated_transformer_reluctance_model_results_directory, integrated_transformer_fem_simulations_results_directory, femmt_working_directory)

    @staticmethod
    def plot_reluctance_model_result_list(valid_design_list: List[ItoSingleResultFile]) -> None:
        """
        Plots the pareto diagram out of the reluctance model calculation.

        :param valid_design_list:
        :type valid_design_list: List[ItoSingleResultFile]
        :return: Plot
        :rtype: None
        """

        volume_list = []
        core_hyst_loss_list = []
        annotation_list = []

        for result in valid_design_list:
            volume_list.append(result.core_2daxi_total_volume)
            core_hyst_loss_list.append(result.total_loss)
            annotation_list.append(result.case)


        fo.plot_2d(volume_list, core_hyst_loss_list, "Volume in m³", "Losses in W", "Pareto Diagram",
                   plot_color="red", annotations=annotation_list)

    @staticmethod
    def plot_filtered_pareto_result_list(filter_volume_list, filter_core_hyst_loss_list):
        fo.plot_2d(filter_volume_list, filter_core_hyst_loss_list, "volume in m³", "core hysteresis losses in W", "Pareto Diagram", plot_color="red")

    # Very slow for many datapo ints.  Fastest for many costs, most readable
    @staticmethod
    def is_pareto_efficient_dumb(costs):
        """
        Find the pareto-efficient points
        :param costs: An (n_points, n_costs) array
        :return: A (n_points, ) boolean array, indicating whether each point is Pareto efficient
        """
        is_efficient = np.ones(costs.shape[0], dtype=bool)
        for i, c in enumerate(costs):
            is_efficient[i] = np.all(np.any(costs[:i] > c, axis=1)) and np.all(np.any(costs[i + 1:] > c, axis=1))
        return is_efficient

    class ReluctanceModel:

        #############################
        # initial optimization
        #############################
        @staticmethod
        def brute_force_calculation(config_file: ItoSingleInputConfig):
            case_number = 0

            # 0. Empty folder
            if os.path.exists(config_file.working_directory):
                shutil.rmtree(config_file.working_directory)

            material_db = mdb.MaterialDatabase(is_silent=True)

            sweep_dto = femmt.IntegratedTransformerOptimization.ReluctanceModel.calculate_sweep_tensors(config_file)

            wire_database = ff.wire_material_database()
            litz_database = ff.litz_database()

            # 1. Extract fundamental frequency from current vectors
            time_extracted, current_extracted_1_vec = fr.time_vec_current_vec_from_time_current_vec(
                sweep_dto.time_current_1_vec)
            time_extracted, current_extracted_2_vec = fr.time_vec_current_vec_from_time_current_vec(
                sweep_dto.time_current_2_vec)
            fundamental_frequency = np.around(1 / time_extracted[-1], decimals=0)
            print(f"{fundamental_frequency = }")

            i_rms_1 = fr.i_rms(sweep_dto.time_current_1_vec)
            i_rms_2 = fr.i_rms(sweep_dto.time_current_2_vec)

            # generate list of all parameter combinations
            t2_core_geometry_sweep = np.array(list(itertools.product(sweep_dto.t1_window_w, sweep_dto.t1_window_h_top,
                                                                     sweep_dto.t1_window_h_bot,
                                                                     sweep_dto.t1_core_inner_diameter)))

            t2_litz_sweep = np.array(
                list(itertools.product(sweep_dto.t1_primary_litz_wire_list, sweep_dto.t1_secondary_litz_wire_list)))

            # report simulation progress
            number_of_simulations = len(sweep_dto.t1_core_material) * len(sweep_dto.t1_window_w) * len(
                sweep_dto.t1_window_h_top) * len(sweep_dto.t1_window_h_bot) * len(
                sweep_dto.t1_core_inner_diameter) * len(
                sweep_dto.t1_n_p_top) * len(sweep_dto.t1_n_p_bot) * len(sweep_dto.t1_n_s_top) * len(
                sweep_dto.t1_n_s_bot)
            number_of_geometry_simulations = len(t2_core_geometry_sweep) * len(sweep_dto.t1_core_material)

            ff.femmt_print(f"{config_file.working_directory = }")
            ff.femmt_print(f"Simulation count: {number_of_simulations} = {number_of_simulations / 1000000000}e9")
            ff.femmt_print(f"Geometry count: {number_of_geometry_simulations}")
            geometry_simulations_per_percent = int(number_of_geometry_simulations / 99)
            ff.femmt_print(f"{geometry_simulations_per_percent = }")
            simulation_progress_percent = 0

            valid_design_list = []

            # initialize parameters staying same form simulation
            t2_inductance_matrix = [
                [sweep_dto.l_s_target_value + sweep_dto.l_h_target_value,
                 sweep_dto.l_h_target_value / sweep_dto.n_target_value],
                [sweep_dto.l_h_target_value / sweep_dto.n_target_value,
                 sweep_dto.l_h_target_value / (sweep_dto.n_target_value ** 2)]]

            geometry_simulation_counter = 0
            for count_core_material, material_name in enumerate(sweep_dto.t1_core_material):
                """
                outer core material loop loads material properties from material database
                 * mu_r_abs
                 * saturation_flux_density and calculates the dimensioning_flux_density from it
                 * material vectors for mu_r_real and mu_r_imag depending on flux_density

                """
                mu_r_abs = material_db.get_material_property(material_name=material_name,
                                                             property="initial_permeability")

                saturation_flux_density = material_db.get_saturation_flux_density(material_name=material_name)
                dimensioning_max_flux_density = saturation_flux_density * sweep_dto.factor_max_flux_density

                print(f"{saturation_flux_density = }")
                print(f"{dimensioning_max_flux_density = }")

                # get material properties, especially mu_r_imag
                material_flux_density_vec, material_mu_r_imag_vec, material_mu_r_real_vec = material_db.permeability_data_to_pro_file(
                    config_file.temperature,
                    fundamental_frequency,
                    material_name,
                    datasource=mdb.MaterialDataSource.ManufacturerDatasheet,
                    datatype='permeability_data',
                    plot_interpolation=False)
                mu_r_abs_vec = np.sqrt(material_mu_r_real_vec ** 2 + material_mu_r_imag_vec ** 2)

                for count_geometry, t1d_core_geometry_material in enumerate(t2_core_geometry_sweep):

                    window_w = t1d_core_geometry_material[0]
                    window_h_top = t1d_core_geometry_material[1]
                    window_h_bot = t1d_core_geometry_material[2]
                    core_inner_diameter = t1d_core_geometry_material[3]

                    # report about simulation progress
                    # if geometry_simulation_counter == geometry_simulations_per_percent * simulation_progress_percent:
                    #     simulation_progress_percent += 1
                    #     print(f"{simulation_progress_percent} simulation_progress_percent")
                    # geometry_simulation_counter += 1
                    simulation_progress_percent = count_geometry / number_of_geometry_simulations * 100
                    print(f"{simulation_progress_percent = } %")

                    # print(geometry_simulation_counter)

                    for primary_litz_wire, secondary_litz_wire in t2_litz_sweep:
                        primary_litz = litz_database[primary_litz_wire]
                        secondary_litz = litz_database[secondary_litz_wire]

                        # cross-section comparison is according to a square for round wire.
                        # this approximation is more realistic
                        # insulation
                        insulation_distance = 1e-3
                        insulation_cross_section_top = 2 * insulation_distance * (window_w + window_h_top)
                        insulation_cross_section_bot = 2 * insulation_distance * (window_w + window_h_bot)

                        total_available_window_cross_section_top = window_h_top * window_w - insulation_cross_section_top
                        for count_n_p_top, n_p_top in enumerate(sweep_dto.t1_n_p_top):
                            winding_cross_section_n_p_top = n_p_top * (2 * primary_litz["conductor_radii"]) ** 2

                            if winding_cross_section_n_p_top > total_available_window_cross_section_top:
                                break

                            for count_n_s_top, n_s_top in enumerate(sweep_dto.t1_n_s_top):
                                winding_cross_section_n_s_top = n_s_top * (2 * secondary_litz["conductor_radii"]) ** 2
                                winding_cross_section_top = winding_cross_section_n_p_top + winding_cross_section_n_s_top
                                if winding_cross_section_top > total_available_window_cross_section_top:
                                    break

                                total_available_window_cross_section_bot = window_h_bot * window_w - insulation_cross_section_bot
                                for count_n_p_bot, n_p_bot in enumerate(sweep_dto.t1_n_p_bot):
                                    winding_cross_section_n_p_bot = n_p_bot * (2 * primary_litz["conductor_radii"]) ** 2
                                    if winding_cross_section_n_p_bot > total_available_window_cross_section_bot:
                                        break
                                    for count_n_s_bot, n_s_bot in enumerate(sweep_dto.t1_n_s_bot):
                                        winding_cross_section_n_s_bot = n_s_bot * (
                                                2 * secondary_litz["conductor_radii"]) ** 2
                                        winding_cross_section_bot = winding_cross_section_n_p_bot + winding_cross_section_n_s_bot

                                        if winding_cross_section_bot > total_available_window_cross_section_bot:
                                            break

                                        core_top_bot_height = core_inner_diameter / 4
                                        core_cross_section = (core_inner_diameter / 2) ** 2 * np.pi

                                        # generate winding matrix
                                        # note that the t11 winding-matrix will be reshaped later!
                                        t2_winding_matrix = [[n_p_top, n_s_top], [n_p_bot, n_s_bot]]

                                        # matrix reshaping
                                        t2_winding_matrix_transpose = np.transpose(t2_winding_matrix, (1, 0))

                                        t2_reluctance_matrix = femmt.IntegratedTransformerOptimization.ReluctanceModel.t2_calculate_reluctance_matrix(
                                            t2_inductance_matrix,
                                            t2_winding_matrix,
                                            t2_winding_matrix_transpose)

                                        if np.linalg.det(t2_reluctance_matrix) != 0 and np.linalg.det(
                                                np.transpose(t2_winding_matrix)) != 0 and np.linalg.det(
                                            t2_inductance_matrix) != 0:
                                            # calculate the flux
                                            flux_top_vec, flux_bot_vec, flux_stray_vec = fr.flux_vec_from_current_vec(
                                                current_extracted_1_vec,
                                                current_extracted_2_vec,
                                                t2_winding_matrix,
                                                t2_inductance_matrix)

                                            # calculate maximum values
                                            flux_top_max, flux_bot_max, flux_stray_max = fr.max_value_from_value_vec(
                                                flux_top_vec, flux_bot_vec,
                                                flux_stray_vec)

                                            flux_density_top_max = flux_top_max / core_cross_section
                                            flux_density_bot_max = flux_bot_max / core_cross_section
                                            flux_density_middle_max = flux_stray_max / core_cross_section

                                            if (flux_density_top_max < dimensioning_max_flux_density) and (
                                                    flux_density_bot_max < dimensioning_max_flux_density) and (
                                                    flux_density_middle_max < dimensioning_max_flux_density):

                                                # calculate target values for r_top and r_bot out of reluctance matrix
                                                r_core_middle_cylinder_radial = fr.r_core_top_bot_radiant(
                                                    core_inner_diameter, window_w, mu_r_abs, core_top_bot_height)

                                                r_middle_target = -t2_reluctance_matrix[0][1]
                                                r_top_target = t2_reluctance_matrix[0][0] - r_middle_target
                                                r_bot_target = t2_reluctance_matrix[1][1] - r_middle_target

                                                # calculate the core reluctance of top and bottom and middle part
                                                r_core_top_cylinder_inner = fr.r_core_round(core_inner_diameter,
                                                                                            window_h_top, mu_r_abs)
                                                r_core_top = 2 * r_core_top_cylinder_inner + r_core_middle_cylinder_radial
                                                r_air_gap_top_target = r_top_target - r_core_top

                                                r_core_bot_cylinder_inner = fr.r_core_round(core_inner_diameter,
                                                                                            window_h_bot, mu_r_abs)
                                                r_core_bot = 2 * r_core_bot_cylinder_inner + r_core_middle_cylinder_radial
                                                r_air_gap_bot_target = r_bot_target - r_core_bot

                                                r_air_gap_middle_target = r_middle_target - r_core_middle_cylinder_radial

                                                minimum_air_gap_length = 1e-6
                                                maximum_air_gap_length = 1e-3
                                                minimum_sort_out_air_gap_length = 100e-6

                                                try:
                                                    l_top_air_gap = optimize.brentq(fr.r_air_gap_round_inf_sct,
                                                                                    minimum_air_gap_length,
                                                                                    maximum_air_gap_length, args=(
                                                            core_inner_diameter, window_h_top, r_air_gap_top_target))
                                                    l_bot_air_gap = optimize.brentq(fr.r_air_gap_round_round_sct,
                                                                                    minimum_air_gap_length,
                                                                                    maximum_air_gap_length, args=(
                                                            core_inner_diameter, window_h_bot / 2, window_h_bot / 2,
                                                            r_air_gap_bot_target))
                                                    l_middle_air_gap = optimize.brentq(fr.r_air_gap_tablet_cylinder_sct,
                                                                                       minimum_air_gap_length,
                                                                                       maximum_air_gap_length, args=(
                                                            core_inner_diameter, core_inner_diameter / 4, window_w,
                                                            r_air_gap_middle_target))
                                                except:
                                                    l_top_air_gap = 0
                                                    l_bot_air_gap = 0
                                                    l_middle_air_gap = 0

                                                if l_top_air_gap > minimum_sort_out_air_gap_length and l_bot_air_gap > minimum_sort_out_air_gap_length and l_middle_air_gap > minimum_sort_out_air_gap_length:
                                                    p_hyst_top = fr.hyst_losses_core_half_mu_r_imag(core_inner_diameter,
                                                                                                    window_h_top,
                                                                                                    window_w,
                                                                                                    mu_r_abs,
                                                                                                    flux_top_max,
                                                                                                    fundamental_frequency,
                                                                                                    material_flux_density_vec,
                                                                                                    material_mu_r_imag_vec)

                                                    p_hyst_middle = fr.power_losses_hysteresis_cylinder_radial_direction_mu_r_imag(
                                                        flux_stray_max, core_inner_diameter / 4,
                                                                        core_inner_diameter / 2,
                                                                        core_inner_diameter / 2 + window_w,
                                                        fundamental_frequency,
                                                        mu_r_abs, material_flux_density_vec, material_mu_r_imag_vec)

                                                    p_hyst_bot = fr.hyst_losses_core_half_mu_r_imag(core_inner_diameter,
                                                                                                    window_h_bot,
                                                                                                    window_w,
                                                                                                    mu_r_abs,
                                                                                                    flux_bot_max,
                                                                                                    fundamental_frequency,
                                                                                                    material_flux_density_vec,
                                                                                                    material_mu_r_imag_vec)

                                                    p_hyst = p_hyst_top + p_hyst_bot + p_hyst_middle

                                                    core_2daxi_total_volume = fr.calculate_core_2daxi_total_volume(
                                                        core_inner_diameter,
                                                        (window_h_bot + window_h_top + core_inner_diameter / 4),
                                                        window_w)

                                                    primary_effective_conductive_cross_section = primary_litz[
                                                                                                     "strands_numbers"] * \
                                                                                                 primary_litz[
                                                                                                     "strand_radii"] ** 2 * np.pi
                                                    primary_effective_conductive_radius = np.sqrt(
                                                        primary_effective_conductive_cross_section / np.pi)
                                                    primary_resistance = fr.resistance_solid_wire(
                                                        core_inner_diameter, window_w,
                                                        n_p_top + n_p_bot,
                                                        primary_effective_conductive_radius,
                                                        material='Copper')
                                                    primary_dc_loss = primary_resistance * i_rms_1 ** 2

                                                    secondary_effective_conductive_cross_section = secondary_litz[
                                                                                                       "strands_numbers"] * \
                                                                                                   secondary_litz[
                                                                                                       "strand_radii"] ** 2 * np.pi
                                                    secondary_effective_conductive_radius = np.sqrt(
                                                        secondary_effective_conductive_cross_section / np.pi)
                                                    secondary_resistance = fr.resistance_solid_wire(
                                                        core_inner_diameter, window_w,
                                                        n_s_top + n_s_bot,
                                                        secondary_effective_conductive_radius,
                                                        material='Copper')
                                                    secondary_dc_loss = secondary_resistance * i_rms_2 ** 2

                                                    total_loss = p_hyst + primary_dc_loss + secondary_dc_loss

                                                    valid_design_dict = ItoSingleResultFile(
                                                        case=case_number,
                                                        air_gap_top=l_top_air_gap,
                                                        air_gap_bot=l_bot_air_gap,
                                                        air_gap_middle=l_middle_air_gap,
                                                        n_p_top=n_p_top,
                                                        n_p_bot=n_p_bot,
                                                        n_s_top=n_s_top,
                                                        n_s_bot=n_s_bot,
                                                        window_h_top=window_h_top,
                                                        window_h_bot=window_h_bot,
                                                        window_w=window_w,
                                                        core_material=material_name,
                                                        core_inner_diameter=core_inner_diameter,
                                                        primary_litz_wire=primary_litz_wire,
                                                        secondary_litz_wire=secondary_litz_wire,
                                                        # results
                                                        flux_top_max=flux_top_max,
                                                        flux_bot_max=flux_bot_max,
                                                        flux_stray_max=flux_stray_max,
                                                        flux_density_top_max=flux_density_top_max,
                                                        flux_density_bot_max=flux_density_bot_max,
                                                        flux_density_stray_max=flux_density_middle_max,
                                                        p_hyst=p_hyst,
                                                        core_2daxi_total_volume=core_2daxi_total_volume,
                                                        primary_litz_wire_loss=primary_dc_loss,
                                                        secondary_litz_wire_loss=secondary_dc_loss,
                                                        total_loss=total_loss

                                                    )

                                                    # Add dict to list of valid designs
                                                    valid_design_list.append(valid_design_dict)
                                                    case_number += 1

            print(f"Number of valid designs: {len(valid_design_list)}")
            return valid_design_list

        @staticmethod
        def t2_calculate_reluctance_matrix(t2_inductance_matrix, t2_winding_matrix, t2_winding_matrix_transpose):
            """
            Calculates the inductance matrix out of reluctance matrix and winding matrix.

            :param t2_inductance_matrix: matrix of transformer inductance
            :param t2_winding_matrix: matrix of transformer windings
            :return: reluctance matrix

            winding matrix e.g.
            N = [ [N_1a, N_2b], [N_1b, N_2b] ]

            inductance matrix e.g.
            L = [ [L_11, M], [M, L_22] ]

            returns reluctance matrix e.g.
            r = [ [], [] ]
            """

            # invert inductance matrix
            t2_inductance_matrix_invert = np.linalg.inv(t2_inductance_matrix)

            # Formular: L = N^T * R^-1 * N
            # Note: Be careful when trying to multiply the matrices in one single step. Some pre-tests failed.
            # The following commented example returns a different result as the code-version. The code-version is
            # verified with a 2x2 example.
            # So this line is not correct!
            # return np.einsum('...ij, ...jj, ...jk -> ...ik', t11_winding_matrix_transpose, t11_reluctance_matrix_invert,
            #                  t11_winding_matrix), t9_valid_design_mask

            # minimal example to understand the operation
            # matrix1 = np.array([[1, 2], [3, 4]])
            # matrix2 = np.array([[5, 6], [7, 8]])
            # matrix3 = np.array([[9, 10], [11, 12]])
            #
            # # reference code
            # normal_multiplication = np.matmul(np.matmul(matrix1, matrix2), matrix3)
            # print(f"{normal_multiplication = }")
            #
            # # This does not macht to the reference code!!!
            # einsum_multiplication = np.einsum('...ij, ...jj, ...jk -> ...ik', matrix1, matrix2, matrix3)
            # print(f"{einsum_multiplication = }")
            #
            # # two einsum multiplications: same result as reference code
            # einsum_multiplication_part_1 = np.einsum('...ij, ...jh -> ...ih', matrix1, matrix2)
            # einsum_multiplication_part_2 = np.einsum('...ij, ...jh -> ...ih', einsum_multiplication_part_1, matrix3)
            # print(f"{einsum_multiplication_part_2 = }")
            einsum_multiplication_part_1 = np.einsum('...ij, ...jh -> ...ih', t2_winding_matrix,
                                                     t2_inductance_matrix_invert)
            einsum_multiplication_part_2 = np.einsum('...ij, ...jh -> ...ih', einsum_multiplication_part_1,
                                                     t2_winding_matrix_transpose)

            return einsum_multiplication_part_2

        @staticmethod
        def calculate_sweep_tensors(input_parameters_dto: ItoSingleInputConfig) -> SweepTensor:
            """
            This method calculates the SweepTensor from the integrated-transformer input config file (ItoSingleInputConfig).

            ItoSingleInputConfig: core_inner_diameter = [10e-3, 30e-3, 5]
            ->> SweepTensor: t1_core_inner_diameter = [10e-3, 15e-3, 20e-3, 25e-3, 30e-3]

            :param input_parameters_dto: integrated transformer input configuration
            :type input_parameters_dto: IntegratedTransformerOptimization
            :return: returns the sweep tensor as mentioned in the example
            :rtype: SweepTensor
            """

            sweep_tensor = SweepTensor(
                # tensors: windings
                t1_n_p_top=np.arange(input_parameters_dto.n_p_top_min_max_list[0],
                                     input_parameters_dto.n_p_top_min_max_list[1] + 1),
                t1_n_p_bot=np.arange(input_parameters_dto.n_p_bot_min_max_list[0],
                                     input_parameters_dto.n_p_bot_min_max_list[1] + 1),
                t1_n_s_top=np.arange(input_parameters_dto.n_s_top_min_max_list[0],
                                     input_parameters_dto.n_s_top_min_max_list[1] + 1),
                t1_n_s_bot=np.arange(input_parameters_dto.n_s_bot_min_max_list[0],
                                     input_parameters_dto.n_s_bot_min_max_list[1] + 1),

                # tensors: outer core geometry and material
                t1_window_h_top=np.linspace(input_parameters_dto.window_h_top_min_max_list[0],
                                            input_parameters_dto.window_h_top_min_max_list[1],
                                            input_parameters_dto.window_h_top_min_max_list[2]),
                t1_window_h_bot=np.linspace(input_parameters_dto.window_h_bot_min_max_list[0],
                                            input_parameters_dto.window_h_bot_min_max_list[1],
                                            input_parameters_dto.window_h_bot_min_max_list[2]),
                t1_window_w=np.linspace(input_parameters_dto.window_w_min_max_list[0],
                                        input_parameters_dto.window_w_min_max_list[1],
                                        input_parameters_dto.window_w_min_max_list[2]),
                t1_core_material=input_parameters_dto.material_list,
                t1_core_inner_diameter=np.linspace(input_parameters_dto.core_inner_diameter_min_max_list[0],
                                                   input_parameters_dto.core_inner_diameter_min_max_list[1],
                                                   input_parameters_dto.core_inner_diameter_min_max_list[2]),

                time_current_1_vec=input_parameters_dto.time_current_1_vec,
                time_current_2_vec=input_parameters_dto.time_current_2_vec,

                l_s_target_value=input_parameters_dto.l_s_target,
                l_h_target_value=input_parameters_dto.l_h_target,
                n_target_value=input_parameters_dto.n_target,
                factor_max_flux_density=input_parameters_dto.factor_max_flux_density,
                t1_primary_litz_wire_list=input_parameters_dto.primary_litz_wire_list,
                t1_secondary_litz_wire_list=input_parameters_dto.secondary_litz_wire_list
            )
            return sweep_tensor

        #############################
        # filters
        #############################

        @staticmethod
        def filter_loss_list(valid_design_list: List[ItoSingleResultFile], factor_min_dc_losses: float = 1.2) -> \
        List[ItoSingleResultFile]:
            # figure out pareto front
            # pareto_volume_list, pareto_core_hyst_list, pareto_dto_list = self.pareto_front(volume_list, core_hyst_loss_list, valid_design_list)

            x_pareto_vec, y_pareto_vec = fo.pareto_front_from_dtos(valid_design_list)

            vector_to_sort = np.array([x_pareto_vec, y_pareto_vec])

            # sorting 2d array by 1st row
            # https://stackoverflow.com/questions/49374253/sort-a-numpy-2d-array-by-1st-row-maintaining-columns
            sorted_vector = vector_to_sort[:, vector_to_sort[0].argsort()]
            x_pareto_vec = sorted_vector[0]
            y_pareto_vec = sorted_vector[1]

            total_losses_list = []
            filtered_design_dto_list = []

            for dto in valid_design_list:
                total_losses_list.append(dto.total_loss)

            min_total_dc_losses = total_losses_list[np.argmin(total_losses_list)]
            loss_offset = factor_min_dc_losses * min_total_dc_losses

            for dto in valid_design_list:
                ref_loss = np.interp(dto.core_2daxi_total_volume, x_pareto_vec, y_pareto_vec) + loss_offset
                if dto.total_loss < ref_loss:
                    filtered_design_dto_list.append(dto)

            return filtered_design_dto_list

        @staticmethod
        def filter_max_air_gap_length(dto_list_to_filter: List[ItoSingleResultFile], max_air_gap_length=1e-6) -> List[
            ItoSingleResultFile]:
            filtered_dtos = []
            for dto in dto_list_to_filter:
                if dto.air_gap_middle < max_air_gap_length and dto.air_gap_top < max_air_gap_length and dto.air_gap_bot < max_air_gap_length:
                    filtered_dtos.append(dto)
            return filtered_dtos

        @staticmethod
        def filter_min_air_gap_length(dto_list_to_filter: List[ItoSingleResultFile], min_air_gap_length=1e-6) -> List[
            ItoSingleResultFile]:
            filtered_dtos = []
            for dto in dto_list_to_filter:
                if dto.air_gap_middle > min_air_gap_length and dto.air_gap_top > min_air_gap_length and dto.air_gap_bot > min_air_gap_length:
                    filtered_dtos.append(dto)
            return filtered_dtos


        #############################
        # save and load
        #############################

        @staticmethod
        def save_dto_list(result_dto_list: List[ItoSingleResultFile], filepath: str):
            """
            Save the ItoSingleResultFile-List to the file structure

            :param result_dto_list:
            :type result_dto_list: List[ItoSingleResultFile]
            :param filepath: filepath
            :type filepath: str
            """
            if not os.path.exists(filepath):
                os.mkdir(filepath)

            for count, dto in enumerate(result_dto_list):
                file_name = os.path.join(filepath, f"case_{dto.case}.json")

                result_dict = dataclasses.asdict(dto)
                with open(file_name, "w+", encoding='utf-8') as outfile:
                    json.dump(result_dict, outfile, indent=2, ensure_ascii=False, cls=MyJSONEncoder)

        @staticmethod
        def save_unfiltered_results(config_file: ItoSingleInputConfig, result_file_list: List[ItoSingleResultFile]):
            """
            Save the results of the reluctance model into the file structure.

            :param config_file: integrated transformer configuration file
            :type config_file: ItoSingleInputConfig
            :param result_file_list: list of ItoSingleResultFiles
            :type result_file_list: List[ItoSingleResultFile]
            """
            # generate folder structure
            femmt.IntegratedTransformerOptimization.set_up_folder_structure(config_file.working_directory)

            # save optimization input parameters
            config_dict = dataclasses.asdict(config_file)

            integrated_transformer_optimization_input_parameters_file = os.path.join(config_file.working_directory, "optimization_input_parameters.json")
            integrated_transformer_reluctance_model_results_directory = os.path.join(config_file.working_directory, "01_reluctance_model_results")

            with open(integrated_transformer_optimization_input_parameters_file, "w+", encoding='utf-8') as outfile:
                json.dump(config_dict, outfile, indent=2, ensure_ascii=False, cls=MyJSONEncoder)

            # save reluctance parameters winning candidates
            femmt.IntegratedTransformerOptimization.ReluctanceModel.save_dto_list(result_file_list, integrated_transformer_reluctance_model_results_directory)

        @staticmethod
        def load_list(filepath: str) -> List[ItoSingleResultFile]:
            """
            Loads the list of the reluctance models from the folder structure.

            :param filepath: filepath
            :type filepath: str
            :return: List of ItoSingleResultFiles
            :rtype: List[ItoSingleResultFile]
            """
            valid_design_list = []
            for file in os.listdir(filepath):
                if file.endswith(".json"):
                    json_file_path = os.path.join(filepath, file)
                    with open(json_file_path, "r") as fd:
                        loaded_data_dict = json.loads(fd.read())

                    valid_design_list.append(result_file_dict_to_dto(loaded_data_dict))
            if len(valid_design_list) == 0:
                raise ValueError("Specified file path is empty")

            return valid_design_list

        @staticmethod
        def load_unfiltered_results(working_directory: str) -> List[ItoSingleResultFile]:
            """
            Loads the results of the reluctance model and returns the ItoSingleResultFiles as a list.

            :param working_directory: working directory
            :type working_directory: str
            :return: List of ItoSingleResultFiles
            :rtype: List[ItoSingleResultFile]
            """
            integrated_transformer_reluctance_model_results_directory = os.path.join(working_directory, "01_reluctance_model_results")
            ff.femmt_print(f"Read results from {integrated_transformer_reluctance_model_results_directory}")
            return femmt.IntegratedTransformerOptimization.ReluctanceModel.load_list(integrated_transformer_reluctance_model_results_directory)

        @staticmethod
        def load_filtered_results(working_directory: str) -> List[ItoSingleResultFile]:
            """
            Loads the results of the reluctance model and returns the ItoSingleResultFiles as a list.

            :param working_directory: working directory
            :type working_directory: str
            :return: List of ItoSingleResultFiles
            :rtype: List[ItoSingleResultFile]
            """
            integrated_transformer_reluctance_model_results_directory = os.path.join(working_directory, "01_reluctance_model_results_filtered")
            ff.femmt_print(f"Read results from {integrated_transformer_reluctance_model_results_directory}")
            return femmt.IntegratedTransformerOptimization.ReluctanceModel.load_list(integrated_transformer_reluctance_model_results_directory)

    class FemSimulation:

        @staticmethod
        def simulate(config_dto: ItoSingleInputConfig, simulation_dto_list: List[ItoSingleResultFile], visualize: bool = False):

            femmt.integrated_transformer_fem_simulations_from_result_dtos(config_dto, simulation_dto_list, visualize)


        @staticmethod
        def filter_loss(fem_simulations_dict_list: List[Dict]):
            """"
            Placeholder function for future filtering the pareto-front
            """

            return fem_simulations_dict_list

        @staticmethod
        def plot_unfiltered_results(working_directory: str) -> None:
            """
            Plots the pareto diagram out of the fem simulation results.

            :param working_directory: filepath to fem simulation results
            :type working_directory: str
            :return: Plot
            :rtype: None
            """

            data_dict_list = []

            volume_list = []
            total_loss_list = []
            total_cost_list = []
            annotation_list = []

            filepath = os.path.join(working_directory, "02_fem_simulation_results")

            for file in os.listdir(filepath):
                if file.endswith(".json"):
                    json_file_path = os.path.join(filepath, file)
                    with open(json_file_path, "r") as fd:
                        loaded_data_dict = json.loads(fd.read())

                    data_dict_list.append(loaded_data_dict)
                    volume_list.append(loaded_data_dict["misc"]["core_2daxi_total_volume"])
                    total_loss_list.append(loaded_data_dict["total_losses"]["all_windings"] + loaded_data_dict["total_losses"]["core"])
                    total_cost_list.append(loaded_data_dict["misc"]["total_cost_incl_margin"])
                    annotation_list.append(f"{file}".replace("case_","").replace(".json",""))

            fo.plot_2d(volume_list, total_loss_list, "Volume in m³", "Losses in W", "Pareto Diagram",
                       plot_color="red", annotations=annotation_list)

        #############################
        # save and load
        #############################

        @staticmethod
        def load_unfiltered_results(filepath: str) -> List[Dict]:
            """
            loads the fem simulations results as a dict into a list.

            :param filepath: file path
            :type filepath: str
            :return:
            :rtype: List[Dict]
            """

            data_dict_list = []

            for file in os.listdir(filepath):
                if file.endswith(".json"):
                    json_file_path = os.path.join(filepath, file)
                    with open(json_file_path, "r") as fd:
                        loaded_data_dict = json.loads(fd.read())

                    data_dict_list.append(loaded_data_dict)
            return data_dict_list


    class ThermalSimulation:
        @staticmethod
        def simulation(config_dto: ItoSingleInputConfig, simulation_dto_list: List[ItoSingleResultFile], visualize: bool = False):
            femmt.integrated_transformer_fem_thermal_simulations_from_result_dtos(config_dto, simulation_dto_list, visualize)

