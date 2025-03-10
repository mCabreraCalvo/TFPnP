#!/usr/bin/env python3
import torch
import torch.utils.data.dataloader
from tensorboardX import SummaryWriter
from pathlib import Path

from env import SPIEnv
from dataset import SPIDataset, SPIEvalDataset
from solver import create_solver_spi

from tfpnp.policy.sync_batchnorm import DataParallelWithCallback
from tfpnp.policy import create_policy_network
from tfpnp.pnp import create_denoiser
from tfpnp.trainer import MDDPGTrainer
from tfpnp.trainer.mddpg.critic import ResNet_wobn
from tfpnp.eval import Evaluator
from tfpnp.utils.noise import GaussianModelD
from tfpnp.utils.options import Options


device = torch.device("cuda" if torch.cuda.is_available() else "cpu")


def main(opt):
    data_dir = Path('data')
    log_dir = Path('log') / opt.exp

    Ks = [4, 6, 8]
    
    base_dim = SPIEnv.ob_base_dim
    actor = create_policy_network(opt, base_dim).to(device)  # policy network
    denoiser = create_denoiser(opt).to(device)
    solver = create_solver_spi(opt, denoiser).to(device)
    num_var = solver.num_var
    
    # ---------------------------------------------------------------------------- #
    #                                     Valid                                    #
    # ---------------------------------------------------------------------------- #
    writer = SummaryWriter(log_dir)
    
    val_roots = [data_dir / 'spi' / 'SPISet13_2020' / f'x{K}' for K in Ks]
    val_datasets = [SPIEvalDataset(val_root, fns=None) for val_root in val_roots]
    
    val_loaders = [torch.utils.data.DataLoader(val_dataset, batch_size=1, shuffle=False,
                                               num_workers=0, pin_memory=True) for val_dataset in val_datasets]
    val_names = [f'spi_x{K}' for K in Ks]
    val_loaders = dict(zip(val_names, val_loaders))
    
    if torch.cuda.device_count() > 1:
        solver = DataParallelWithCallback(solver)

    eval_env = SPIEnv(None, solver, max_episode_step=opt.max_episode_step, device=device)
    evaluator = Evaluator(opt, eval_env, val_loaders, writer, device)
    
    if opt.eval:
        actor_ckpt = torch.load(opt.resume)
        actor.load_state_dict(actor_ckpt)
        evaluator.eval(actor, step=opt.resume_step)
        return
    
    # ---------------------------------------------------------------------------- #
    #                                     Train                                    #
    # ---------------------------------------------------------------------------- #
    
    sigma_ns = [5, 10, 15]
    noise_model = GaussianModelD(sigma_ns)

    train_root = data_dir / 'Images_128'
    
    train_dataset = SPIDataset(train_root, fns=None, Ks=Ks)
    
    train_loader = torch.utils.data.DataLoader(
        train_dataset, batch_size=opt.env_batch, shuffle=True,
        num_workers=opt.num_workers, pin_memory=True, drop_last=True)

    env = SPIEnv(train_loader, solver, max_episode_step=opt.max_episode_step, device=device)

    def lr_scheduler(step):
        if step < 10000:
            return {'critic': 1e-4, 'actor': 5e-5}
        else:
            return {'critic': 5e-5, 'actor': 1e-5}    

    critic = ResNet_wobn(base_dim+num_var, 18, 1).to(device)
    critic_target = ResNet_wobn(base_dim+num_var, 18, 1).to(device)

    trainer = MDDPGTrainer(opt, env, actor=actor,
                           critic=critic, critic_target=critic_target,
                           lr_scheduler=lr_scheduler, device=device,
                           evaluator=evaluator, writer=writer)
    trainer.train()


if __name__ == "__main__":
    option = Options()
    opt = option.parse()
    main(opt)
